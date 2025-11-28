"""SQLite database for tracking improvements with planning-first workflow."""
import sqlite3
import json
import threading
from contextlib import contextmanager
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

    # Class-level registry of active databases for isolated instances
    _instances: Dict[str, 'Database'] = {}
    _instance_lock = threading.Lock()

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @classmethod
    def get_isolated_instance(cls, base_path: Path, env_id: str) -> 'Database':
        """
        Get or create isolated database instance for test environment.

        Args:
            base_path: Base path for test data
            env_id: Environment identifier

        Returns:
            Isolated Database instance
        """
        with cls._instance_lock:
            if env_id not in cls._instances:
                db_path = base_path / 'test_data' / env_id / 'improvements.db'
                db_path.parent.mkdir(parents=True, exist_ok=True)
                cls._instances[env_id] = cls(db_path)
            return cls._instances[env_id]

    @classmethod
    def release_isolated_instance(cls, env_id: str):
        """
        Release and cleanup isolated database instance.

        Args:
            env_id: Environment identifier to release
        """
        with cls._instance_lock:
            if env_id in cls._instances:
                db = cls._instances[env_id]
                # Close connections and cleanup
                try:
                    if db.db_path.exists():
                        db.db_path.unlink()
                        # Try to remove parent directory if empty
                        try:
                            db.db_path.parent.rmdir()
                        except OSError:
                            pass  # Directory not empty
                except Exception as e:
                    logger.warning(f"Error cleaning up database for {env_id}: {e}")
                del cls._instances[env_id]

    def _init_db(self):
        """Initialize database schema for planning workflow."""
        # Use longer timeout and WAL mode for better concurrency
        with sqlite3.connect(str(self.db_path), timeout=30.0) as conn:
            # Enable WAL mode for better concurrency
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA busy_timeout=30000')  # 30 seconds
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
                'ALTER TABLE improvements ADD COLUMN optimized_plan TEXT',  # Summary of key features
                'ALTER TABLE improvements ADD COLUMN discovery_source TEXT',
                'ALTER TABLE improvements ADD COLUMN discovery_metadata TEXT',
                'ALTER TABLE improvements ADD COLUMN original_plan_id INTEGER',
                'ALTER TABLE improvements ADD COLUMN discovery_timestamp TEXT',
                'ALTER TABLE improvements ADD COLUMN confidence_score REAL DEFAULT 0.5',
            ]

            for migration in migrations:
                try:
                    conn.execute(migration)
                except sqlite3.OperationalError:
                    pass  # Column already exists

            conn.execute('CREATE INDEX IF NOT EXISTS idx_status ON improvements(status)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_priority ON improvements(priority)')
            conn.commit()

    @contextmanager
    def get_connection(self):
        """Context manager for database connections with proper cleanup."""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        try:
            conn.execute('PRAGMA journal_mode=WAL')
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

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

    def save_plan(self, imp_id: int, plan_content: str, optimized_plan: str = '') -> bool:
        """Save the generated plan and move to review status."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                UPDATE improvements
                SET plan_content = ?, plan_status = 'reviewing', status = 'plan_review',
                    optimized_plan = ?
                WHERE id = ?
            ''', (plan_content, optimized_plan, imp_id))
            conn.commit()
            logger.info(f"Plan saved for #{imp_id}, awaiting review")
            return True

    def update_optimized_plan(self, imp_id: int, optimized_plan: str) -> bool:
        """Update the optimized plan summary."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                UPDATE improvements
                SET optimized_plan = ?
                WHERE id = ?
            ''', (optimized_plan, imp_id))
            conn.commit()
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

    def add_discovered(self, title: str, description: str, category: str,
                       priority: int, discovery_source: str, metadata: Dict,
                       confidence: float = 0.5) -> int:
        """Add a discovered improvement with metadata."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                INSERT INTO improvements
                (title, description, category, priority, source, created_at, status,
                 discovery_source, discovery_metadata, discovery_timestamp, confidence_score)
                VALUES (?, ?, ?, ?, 'ai_discovered', ?, 'pending', ?, ?, ?, ?)
            ''', (title, description, category, priority, datetime.now().isoformat(),
                  discovery_source, json.dumps(metadata), datetime.now().isoformat(),
                  confidence))
            conn.commit()
            return cursor.lastrowid

    def get_plan_for_reuse(self, imp_id: int) -> Optional[str]:
        """Get original plan for a task (for retry reuse)."""
        with sqlite3.connect(self.db_path) as conn:
            # First check if this task has an original_plan_id
            cursor = conn.execute(
                'SELECT original_plan_id, plan_content FROM improvements WHERE id = ?',
                (imp_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None

            original_id, plan_content = row

            # If there's an original plan reference, fetch that
            if original_id:
                cursor = conn.execute(
                    'SELECT plan_content FROM improvements WHERE id = ?',
                    (original_id,)
                )
                original_row = cursor.fetchone()
                if original_row and original_row[0]:
                    return original_row[0]

            # Otherwise return this task's own plan
            return plan_content

    def link_to_original_plan(self, new_id: int, original_id: int) -> bool:
        """Link a retried task to its original plan."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                'UPDATE improvements SET original_plan_id = ? WHERE id = ?',
                (original_id, new_id)
            )
            conn.commit()
            return True

    def get_discoveries_by_category(self, category: str) -> List[Dict]:
        """Get all discovered improvements by category."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('''
                SELECT * FROM improvements
                WHERE discovery_source IS NOT NULL AND category = ?
                ORDER BY priority DESC
            ''', (category,))
            return [dict(row) for row in cursor.fetchall()]

    def get_discovery_stats(self) -> Dict:
        """Get statistics about discovered improvements."""
        with sqlite3.connect(self.db_path) as conn:
            stats = {}
            cursor = conn.execute('''
                SELECT discovery_source, COUNT(*) as count
                FROM improvements
                WHERE discovery_source IS NOT NULL
                GROUP BY discovery_source
            ''')
            for row in cursor.fetchall():
                stats[row[0]] = row[1]
            return stats
