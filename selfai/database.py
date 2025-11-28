"""SQLite database for tracking improvements with planning-first workflow."""
import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import logging

logger = logging.getLogger('selfai')

# Maximum test attempts before marking as cancelled
MAX_TEST_ATTEMPTS = 3

# Maximum parallel tasks
MAX_PARALLEL_TASKS = 3

# Status flow:
# pending -> planning -> plan_review -> approved -> in_progress -> testing -> completed
#                           |                                         |
#                     needs_feedback                              failed (retry)
#                                                                     |
#                                                                cancelled (after 3 failures)

VALID_STATUSES = [
    'pending',        # New task, waiting to be planned
    'planning',       # Currently generating plan
    'plan_review',    # Plan ready for user review
    'approved',       # User approved, ready to execute
    'in_progress',    # Currently being implemented
    'testing',        # Being tested
    'completed',      # Successfully completed
    'failed',         # Test failed, will retry
    'cancelled',      # Failed 3 times, needs user feedback
]


class Database:
    """SQLite database manager for improvements with planning-first workflow."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Initialize database schema for planning workflow."""
        with sqlite3.connect(self.db_path) as conn:
            # Create new simplified table
            conn.execute('''
            CREATE TABLE IF NOT EXISTS improvements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                category TEXT DEFAULT 'general',
                priority INTEGER DEFAULT 50,
                source TEXT DEFAULT 'ai_discovered',
                created_at TEXT,

                -- Status
                status TEXT DEFAULT 'pending',

                -- Planning
                plan_content TEXT,
                plan_status TEXT DEFAULT 'pending',
                user_feedback TEXT,

                -- Execution
                output TEXT,
                test_output TEXT,
                test_count INTEGER DEFAULT 0,

                -- Timestamps
                started_at TEXT,
                completed_at TEXT,

                -- Error tracking
                error TEXT,
                last_error TEXT
            )
            ''')

            # Migrations for existing databases (add new columns if missing)
            migrations = [
                'ALTER TABLE improvements ADD COLUMN plan_content TEXT',
                'ALTER TABLE improvements ADD COLUMN plan_status TEXT DEFAULT "pending"',
                'ALTER TABLE improvements ADD COLUMN user_feedback TEXT',
                'ALTER TABLE improvements ADD COLUMN output TEXT',
                'ALTER TABLE improvements ADD COLUMN test_output TEXT',
                'ALTER TABLE improvements ADD COLUMN test_count INTEGER DEFAULT 0',
                'ALTER TABLE improvements ADD COLUMN completed_at TEXT',
                'ALTER TABLE improvements ADD COLUMN last_error TEXT',
            ]

            for migration in migrations:
                try:
                    conn.execute(migration)
                except sqlite3.OperationalError:
                    pass  # Column already exists

            conn.execute('CREATE INDEX IF NOT EXISTS idx_status ON improvements(status)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_priority ON improvements(priority)')
            conn.commit()

    def add(self, title: str, description: str, category: str = 'general',
            priority: int = 50, source: str = 'ai_discovered') -> int:
        """Add a new improvement."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                INSERT INTO improvements (title, description, category, priority, source, created_at, status)
                VALUES (?, ?, ?, ?, ?, ?, 'pending')
            ''', (title, description, category, priority, source, datetime.now().isoformat()))
            conn.commit()
            logger.info(f"Added improvement #{cursor.lastrowid}: {title}")
            return cursor.lastrowid

    def get_by_id(self, imp_id: int) -> Optional[Dict]:
        """Get a single improvement by ID."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('SELECT * FROM improvements WHERE id = ?', (imp_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_all(self) -> List[Dict]:
        """Get all improvements."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('SELECT * FROM improvements ORDER BY priority DESC, id DESC')
            return [dict(row) for row in cursor.fetchall()]

    def get_pending_planning(self, limit: int = MAX_PARALLEL_TASKS) -> List[Dict]:
        """Get tasks that need planning."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('''
                SELECT * FROM improvements
                WHERE status = 'pending'
                ORDER BY priority DESC, created_at ASC
                LIMIT ?
            ''', (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def get_approved_tasks(self, limit: int = MAX_PARALLEL_TASKS) -> List[Dict]:
        """Get tasks that are approved and ready for execution."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('''
                SELECT * FROM improvements
                WHERE status = 'approved'
                ORDER BY priority DESC, created_at ASC
                LIMIT ?
            ''', (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def get_tasks_for_testing(self, limit: int = MAX_PARALLEL_TASKS) -> List[Dict]:
        """Get tasks that need testing (only tasks that were implemented)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('''
                SELECT * FROM improvements
                WHERE (status = 'testing' OR status = 'failed')
                AND test_count < ?
                AND output IS NOT NULL
                ORDER BY priority DESC
                LIMIT ?
            ''', (MAX_TEST_ATTEMPTS, limit))
            return [dict(row) for row in cursor.fetchall()]

    def get_in_progress(self, limit: int = MAX_PARALLEL_TASKS) -> List[Dict]:
        """Get tasks currently in progress."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('''
                SELECT * FROM improvements
                WHERE status = 'in_progress'
                ORDER BY started_at ASC
                LIMIT ?
            ''', (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def get_plan_review_tasks(self) -> List[Dict]:
        """Get tasks waiting for plan review/approval."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('''
                SELECT * FROM improvements
                WHERE status = 'plan_review'
                ORDER BY priority DESC, created_at ASC
            ''')
            return [dict(row) for row in cursor.fetchall()]

    def get_cancelled_tasks(self) -> List[Dict]:
        """Get cancelled tasks (need user feedback to re-enable)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('''
                SELECT * FROM improvements
                WHERE status = 'cancelled'
                ORDER BY priority DESC
            ''')
            return [dict(row) for row in cursor.fetchall()]

    # Status transitions
    def mark_planning(self, imp_id: int) -> bool:
        """Mark task as currently being planned."""
        return self._update_status(imp_id, 'planning')

    def save_plan(self, imp_id: int, plan_content: str) -> bool:
        """Save the generated plan and move to review status."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                UPDATE improvements
                SET plan_content = ?, plan_status = 'reviewing', status = 'plan_review'
                WHERE id = ?
            ''', (plan_content, imp_id))
            conn.commit()
            logger.info(f"Plan saved for #{imp_id}, awaiting review")
            return True

    def approve_plan(self, imp_id: int) -> bool:
        """User approves the plan - ready for execution."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                UPDATE improvements
                SET plan_status = 'approved', status = 'approved'
                WHERE id = ?
            ''', (imp_id,))
            conn.commit()
            logger.info(f"Plan approved for #{imp_id}")
            return True

    def request_plan_feedback(self, imp_id: int, feedback: str) -> bool:
        """User requests changes to the plan."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                UPDATE improvements
                SET plan_status = 'needs_feedback', user_feedback = ?, status = 'pending'
                WHERE id = ?
            ''', (feedback, imp_id))
            conn.commit()
            logger.info(f"Feedback requested for #{imp_id}: {feedback[:50]}...")
            return True

    def mark_in_progress(self, imp_id: int) -> bool:
        """Mark task as in progress (being implemented)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                UPDATE improvements
                SET status = 'in_progress', started_at = ?
                WHERE id = ?
            ''', (datetime.now().isoformat(), imp_id))
            conn.commit()
            return True

    def mark_testing(self, imp_id: int, output: str = '') -> bool:
        """Mark task as ready for testing."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                UPDATE improvements
                SET status = 'testing', output = ?
                WHERE id = ?
            ''', (output, imp_id))
            conn.commit()
            return True

    def mark_test_passed(self, imp_id: int, test_output: str = '') -> bool:
        """Test passed - mark as completed."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                UPDATE improvements
                SET status = 'completed', test_output = ?, completed_at = ?
                WHERE id = ?
            ''', (test_output, datetime.now().isoformat(), imp_id))
            conn.commit()
            logger.info(f"Feature #{imp_id} completed successfully!")
            return True

    def mark_test_failed(self, imp_id: int, test_output: str = '') -> bool:
        """Test failed - increment count and check if should be cancelled."""
        with sqlite3.connect(self.db_path) as conn:
            # Get current test count
            cursor = conn.execute('SELECT test_count FROM improvements WHERE id = ?', (imp_id,))
            row = cursor.fetchone()
            if not row:
                return False

            current_count = (row[0] or 0) + 1

            if current_count >= MAX_TEST_ATTEMPTS:
                # 3 failures = cancelled
                conn.execute('''
                    UPDATE improvements
                    SET status = 'cancelled', test_count = ?, test_output = ?,
                        error = 'Cancelled after 3 test failures'
                    WHERE id = ?
                ''', (current_count, test_output, imp_id))
                logger.warning(f"Feature #{imp_id} cancelled after {current_count} test failures")
            else:
                # Still has retries left - mark as failed for retry
                conn.execute('''
                    UPDATE improvements
                    SET status = 'failed', test_count = ?, test_output = ?,
                        last_error = ?
                    WHERE id = ?
                ''', (current_count, test_output, test_output[:500] if test_output else '', imp_id))
                logger.info(f"Feature #{imp_id} test failed ({current_count}/{MAX_TEST_ATTEMPTS})")

            conn.commit()
            return True

    def re_enable_cancelled(self, imp_id: int, feedback: str = '') -> bool:
        """Re-enable a cancelled task with optional feedback."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                UPDATE improvements
                SET status = 'pending', test_count = 0, user_feedback = ?,
                    plan_status = 'needs_feedback', error = NULL
                WHERE id = ? AND status = 'cancelled'
            ''', (feedback, imp_id))
            conn.commit()
            logger.info(f"Re-enabled cancelled feature #{imp_id}")
            return True

    def mark_failed(self, imp_id: int, error: str) -> bool:
        """Mark task as failed with error."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                UPDATE improvements
                SET status = 'failed', error = ?, last_error = ?
                WHERE id = ?
            ''', (error, error[:500], imp_id))
            conn.commit()
            return True

    def _update_status(self, imp_id: int, status: str) -> bool:
        """Update task status."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('UPDATE improvements SET status = ? WHERE id = ?', (status, imp_id))
            conn.commit()
            return True

    def get_stats(self) -> Dict:
        """Get statistics."""
        with sqlite3.connect(self.db_path) as conn:
            stats = {}
            for status in VALID_STATUSES:
                cursor = conn.execute("SELECT COUNT(*) FROM improvements WHERE status = ?", (status,))
                stats[status] = cursor.fetchone()[0]
            cursor = conn.execute("SELECT COUNT(*) FROM improvements")
            stats['total'] = cursor.fetchone()[0]
            return stats

    def exists(self, title: str) -> bool:
        """Check if improvement with title already exists."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT 1 FROM improvements WHERE title = ?", (title,))
            return cursor.fetchone() is not None

    def get_active_count(self) -> int:
        """Get count of active tasks (in_progress + testing)."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                SELECT COUNT(*) FROM improvements
                WHERE status IN ('in_progress', 'testing', 'planning')
            ''')
            return cursor.fetchone()[0]

    def can_start_new_task(self) -> bool:
        """Check if we can start a new task (under parallel limit)."""
        return self.get_active_count() < MAX_PARALLEL_TASKS
