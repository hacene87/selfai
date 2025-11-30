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
                # Worktree tracking columns
                'ALTER TABLE improvements ADD COLUMN worktree_path TEXT',
                'ALTER TABLE improvements ADD COLUMN branch_name TEXT',
                'ALTER TABLE improvements ADD COLUMN merge_conflicts TEXT',
                # 3-level complexity system columns
                'ALTER TABLE improvements ADD COLUMN current_level INTEGER DEFAULT 1',
                'ALTER TABLE improvements ADD COLUMN mvp_status TEXT DEFAULT "pending"',
                'ALTER TABLE improvements ADD COLUMN mvp_output TEXT',
                'ALTER TABLE improvements ADD COLUMN mvp_test_output TEXT',
                'ALTER TABLE improvements ADD COLUMN mvp_test_count INTEGER DEFAULT 0',
                'ALTER TABLE improvements ADD COLUMN enhanced_status TEXT DEFAULT "locked"',
                'ALTER TABLE improvements ADD COLUMN enhanced_output TEXT',
                'ALTER TABLE improvements ADD COLUMN enhanced_test_output TEXT',
                'ALTER TABLE improvements ADD COLUMN enhanced_test_count INTEGER DEFAULT 0',
                'ALTER TABLE improvements ADD COLUMN advanced_status TEXT DEFAULT "locked"',
                'ALTER TABLE improvements ADD COLUMN advanced_output TEXT',
                'ALTER TABLE improvements ADD COLUMN advanced_test_output TEXT',
                'ALTER TABLE improvements ADD COLUMN advanced_test_count INTEGER DEFAULT 0',
                # Log analysis and diagnostics columns
                'ALTER TABLE improvements ADD COLUMN diagnosed_issues INTEGER DEFAULT 0',
                'ALTER TABLE improvements ADD COLUMN auto_fixed_issues INTEGER DEFAULT 0',
                'ALTER TABLE improvements ADD COLUMN diagnostic_confidence REAL DEFAULT 0.0',
            ]

            for migration in migrations:
                try:
                    conn.execute(migration)
                except sqlite3.OperationalError:
                    pass  # Column already exists

            conn.execute('CREATE INDEX IF NOT EXISTS idx_status ON improvements(status)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_priority ON improvements(priority)')

            # Create level_unlocks table for global unlock tracking
            conn.execute('''
                CREATE TABLE IF NOT EXISTS level_unlocks (
                    level TEXT PRIMARY KEY,
                    unlocked_at TEXT,
                    required_count INTEGER,
                    completed_count INTEGER DEFAULT 0
                )
            ''')

            # Initialize unlock requirements
            conn.execute('INSERT OR IGNORE INTO level_unlocks VALUES ("enhanced", NULL, 5, 0)')
            conn.execute('INSERT OR IGNORE INTO level_unlocks VALUES ("advanced", NULL, 10, 0)')

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

    def get_stuck_in_progress_tasks(self, limit: int = MAX_PARALLEL_TASKS) -> List[Dict]:
        """Get in-progress tasks that may have crashed (oldest first by started_at).

        These are tasks marked as in_progress but no active runner is processing them.
        Order by started_at ASC to process oldest stuck tasks first.
        """
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
        """Save the generated plan and auto-approve for execution."""
        with sqlite3.connect(self.db_path) as conn:
            # Get current level to set the right level status
            cursor = conn.execute('SELECT current_level FROM improvements WHERE id = ?', (imp_id,))
            row = cursor.fetchone()
            level = row[0] if row else 1
            level_status_col = {1: 'mvp_status', 2: 'enhanced_status', 3: 'advanced_status'}.get(level, 'mvp_status')

            conn.execute(f'''
                UPDATE improvements
                SET plan_content = ?, plan_status = 'approved', status = 'approved',
                    optimized_plan = ?, {level_status_col} = 'approved'
                WHERE id = ?
            ''', (plan_content, optimized_plan, imp_id))
            conn.commit()
            logger.info(f"Plan saved and auto-approved for #{imp_id}")
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
            # Get current status to log transition
            cursor = conn.execute('SELECT status, title FROM improvements WHERE id = ?', (imp_id,))
            row = cursor.fetchone()
            if row:
                old_status = row[0]
                title = row[1]
                logger.info(f"Task #{imp_id} ({title}): {old_status} → in_progress")

            conn.execute('''
                UPDATE improvements
                SET status = 'in_progress', started_at = ?
                WHERE id = ?
            ''', (datetime.now().isoformat(), imp_id))
            conn.commit()
            return True

    def mark_testing(self, imp_id: int, output: str = '') -> bool:
        """Mark task as ready for testing and update level status."""
        with sqlite3.connect(self.db_path) as conn:
            # Get current level to update the appropriate level status
            cursor = conn.execute('SELECT current_level FROM improvements WHERE id = ?', (imp_id,))
            row = cursor.fetchone()
            current_level = row[0] if row else 1

            # Update level status based on current level
            level_status_col = {1: 'mvp_status', 2: 'enhanced_status', 3: 'advanced_status'}[current_level]

            conn.execute(f'''
                UPDATE improvements
                SET status = 'testing', output = ?, {level_status_col} = 'testing'
                WHERE id = ?
            ''', (output, imp_id))
            conn.commit()
            return True

    def mark_test_passed(self, imp_id: int, test_output: str = '') -> bool:
        """Test passed - mark as completed and update level status."""
        with sqlite3.connect(self.db_path) as conn:
            # Get current level to update the appropriate level status
            cursor = conn.execute('SELECT current_level FROM improvements WHERE id = ?', (imp_id,))
            row = cursor.fetchone()
            current_level = row[0] if row else 1

            # Determine which level statuses to mark as completed
            level_updates = []
            if current_level >= 1:
                level_updates.append("mvp_status = 'completed'")
            if current_level >= 2:
                level_updates.append("enhanced_status = 'completed'")
            if current_level >= 3:
                level_updates.append("advanced_status = 'completed'")

            level_sql = ", ".join(level_updates) if level_updates else ""
            if level_sql:
                level_sql = ", " + level_sql

            conn.execute(f'''
                UPDATE improvements
                SET status = 'completed', test_output = ?, completed_at = ?{level_sql}
                WHERE id = ?
            ''', (test_output, datetime.now().isoformat(), imp_id))
            conn.commit()
            logger.info(f"Feature #{imp_id} completed successfully at level {current_level}!")
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

    def record_diagnosis(self, imp_id: int, confidence: float, fixed: bool = False) -> bool:
        """Record diagnostic attempt for log analysis.

        Args:
            imp_id: Improvement ID
            confidence: Diagnostic confidence score (0.0-1.0)
            fixed: Whether the issue was auto-fixed

        Returns:
            True if successful
        """
        with sqlite3.connect(self.db_path) as conn:
            if fixed:
                conn.execute('''
                    UPDATE improvements
                    SET diagnosed_issues = diagnosed_issues + 1,
                        auto_fixed_issues = auto_fixed_issues + 1,
                        diagnostic_confidence = ?
                    WHERE id = ?
                ''', (confidence, imp_id))
            else:
                conn.execute('''
                    UPDATE improvements
                    SET diagnosed_issues = diagnosed_issues + 1,
                        diagnostic_confidence = ?
                    WHERE id = ?
                ''', (confidence, imp_id))
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

    def exists(self, title: str, similarity_threshold: float = 0.7) -> bool:
        """Check if improvement with title or similar title already exists.

        Uses fuzzy matching to catch near-duplicates like:
        - "Add retry logic" vs "Implement retry logic"
        - "Add health check" vs "Add health check endpoint"
        """
        from difflib import SequenceMatcher

        # Normalize title for comparison
        title_normalized = title.lower().strip()

        # Extract key words (remove common prefixes/suffixes)
        key_words = set(title_normalized.replace('implement', '').replace('add', '')
                       .replace('create', '').replace('for', '').replace('to', '')
                       .replace('the', '').replace('and', '').replace('with', '').split())

        with sqlite3.connect(self.db_path) as conn:
            # Exact match first
            cursor = conn.execute("SELECT 1 FROM improvements WHERE title = ?", (title,))
            if cursor.fetchone() is not None:
                return True

            # Get all existing titles for fuzzy matching
            cursor = conn.execute("SELECT title FROM improvements WHERE status != 'cancelled'")
            existing_titles = [row[0] for row in cursor.fetchall()]

            for existing in existing_titles:
                existing_normalized = existing.lower().strip()

                # Check string similarity
                similarity = SequenceMatcher(None, title_normalized, existing_normalized).ratio()
                if similarity >= similarity_threshold:
                    return True

                # Check key word overlap
                existing_words = set(existing_normalized.replace('implement', '').replace('add', '')
                                    .replace('create', '').replace('for', '').replace('to', '')
                                    .replace('the', '').replace('and', '').replace('with', '').split())
                if key_words and existing_words:
                    overlap = len(key_words & existing_words) / max(len(key_words), len(existing_words))
                    if overlap >= 0.6:  # 60% word overlap = likely duplicate
                        return True

            return False

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

    def set_worktree_info(self, imp_id: int, worktree_path: str, branch_name: str) -> bool:
        """Store worktree metadata for task.

        Args:
            imp_id: Improvement ID
            worktree_path: Path to worktree
            branch_name: Git branch name

        Returns:
            True if successful
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                'UPDATE improvements SET worktree_path = ?, branch_name = ? WHERE id = ?',
                (worktree_path, branch_name, imp_id)
            )
            conn.commit()
            logger.info(f"Worktree info saved for #{imp_id}: {branch_name}")
            return True

    def record_merge_conflict(self, imp_id: int, conflicted_files: List[str]) -> bool:
        """Record merge conflict details.

        Args:
            imp_id: Improvement ID
            conflicted_files: List of files with conflicts

        Returns:
            True if successful
        """
        conflict_str = json.dumps(conflicted_files)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                'UPDATE improvements SET merge_conflicts = ? WHERE id = ?',
                (conflict_str, imp_id)
            )
            conn.commit()
            logger.warning(f"Recorded merge conflicts for #{imp_id}: {len(conflicted_files)} files")
            return True

    def clear_worktree_info(self, imp_id: int) -> bool:
        """Clear worktree metadata after cleanup.

        Args:
            imp_id: Improvement ID

        Returns:
            True if successful
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                'UPDATE improvements SET worktree_path = NULL, branch_name = NULL WHERE id = ?',
                (imp_id,)
            )
            conn.commit()
            return True

    # 3-Level Complexity System Methods

    def is_level_unlocked(self, level: int) -> tuple[bool, str]:
        """Check if a level is unlocked globally."""
        if level == 1:
            return True, 'MVP level is always available'

        with sqlite3.connect(self.db_path) as conn:
            level_name = 'enhanced' if level == 2 else 'advanced'
            cursor = conn.execute(
                'SELECT unlocked_at, required_count, completed_count FROM level_unlocks WHERE level = ?',
                (level_name,)
            )
            row = cursor.fetchone()
            if row and row[0]:  # unlocked_at is set
                return True, f'{level_name.title()} level unlocked!'
            elif row:
                return False, f'{level_name.title()} requires {row[1]} tested features at previous level ({row[2]}/{row[1]} complete)'
            return False, 'Unknown level'

    def check_and_unlock_levels(self):
        """Check if any levels should be unlocked based on completed features."""
        with sqlite3.connect(self.db_path) as conn:
            # Count features with passed MVP tests
            cursor = conn.execute(
                "SELECT COUNT(*) FROM improvements WHERE mvp_status = 'completed'"
            )
            mvp_completed = cursor.fetchone()[0]

            # Count features with passed Enhanced tests
            cursor = conn.execute(
                "SELECT COUNT(*) FROM improvements WHERE enhanced_status = 'completed'"
            )
            enhanced_completed = cursor.fetchone()[0]

            # Update counts and unlock if thresholds met
            conn.execute(
                'UPDATE level_unlocks SET completed_count = ? WHERE level = ?',
                (mvp_completed, 'enhanced')
            )
            conn.execute(
                'UPDATE level_unlocks SET completed_count = ? WHERE level = ?',
                (enhanced_completed, 'advanced')
            )

            # Check Enhanced unlock (5 MVPs)
            if mvp_completed >= 5:
                conn.execute(
                    'UPDATE level_unlocks SET unlocked_at = ? WHERE level = ? AND unlocked_at IS NULL',
                    (datetime.now().isoformat(), 'enhanced')
                )

            # Check Advanced unlock (10 Enhanced)
            if enhanced_completed >= 10:
                conn.execute(
                    'UPDATE level_unlocks SET unlocked_at = ? WHERE level = ? AND unlocked_at IS NULL',
                    (datetime.now().isoformat(), 'advanced')
                )

            conn.commit()

    def get_features_for_level(self, level: int, limit: int = MAX_PARALLEL_TASKS) -> List[Dict]:
        """Get features ready for implementation at a specific level."""
        level_status_col = {1: 'mvp_status', 2: 'enhanced_status', 3: 'advanced_status'}[level]

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            # Features at this level that are approved/ready (exclude already processing)
            cursor = conn.execute(f'''
                SELECT * FROM improvements
                WHERE current_level = ? AND {level_status_col} = 'approved'
                AND status = 'approved'
                ORDER BY priority DESC
                LIMIT ?
            ''', (level, limit))
            return [dict(row) for row in cursor.fetchall()]

    def advance_to_next_level(self, imp_id: int) -> bool:
        """Advance a feature to the next level after passing tests."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('SELECT current_level FROM improvements WHERE id = ?', (imp_id,))
            row = cursor.fetchone()
            if not row:
                return False

            current = row[0]
            if current >= 3:
                return False  # Already at max level

            next_level = current + 1
            next_status_col = {2: 'enhanced_status', 3: 'advanced_status'}[next_level]

            conn.execute(f'''
                UPDATE improvements
                SET current_level = ?, {next_status_col} = 'pending'
                WHERE id = ?
            ''', (next_level, imp_id))
            conn.commit()
            return True

    def mark_level_completed(self, imp_id: int, level: int, output: str) -> bool:
        """Mark a level's implementation as complete, ready for testing."""
        cols = {1: ('mvp_status', 'mvp_output'), 2: ('enhanced_status', 'enhanced_output'), 3: ('advanced_status', 'advanced_output')}
        status_col, output_col = cols[level]

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(f'''
                UPDATE improvements SET {status_col} = 'testing', {output_col} = ?
                WHERE id = ?
            ''', (output, imp_id))
            conn.commit()
            return True

    def mark_level_test_passed(self, imp_id: int, level: int, test_output: str) -> bool:
        """Mark a level's tests as passed."""
        cols = {1: ('mvp_status', 'mvp_test_output'), 2: ('enhanced_status', 'enhanced_test_output'), 3: ('advanced_status', 'advanced_test_output')}
        status_col, test_col = cols[level]

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(f'''
                UPDATE improvements SET {status_col} = 'completed', {test_col} = ?
                WHERE id = ?
            ''', (test_output, imp_id))
            conn.commit()

            # Check if feature is fully complete (all 3 levels)
            if level == 3:
                conn.execute('''
                    UPDATE improvements SET status = 'completed', completed_at = ?
                    WHERE id = ?
                ''', (datetime.now().isoformat(), imp_id))
                conn.commit()

            # Check if any new levels should be unlocked
            self.check_and_unlock_levels()
            return True

    def get_pending_planning_for_level(self, level: int, limit: int = MAX_PARALLEL_TASKS) -> List[Dict]:
        """Get features that need planning at a specific level."""
        level_status_col = {1: 'mvp_status', 2: 'enhanced_status', 3: 'advanced_status'}[level]

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(f'''
                SELECT * FROM improvements
                WHERE current_level = ? AND {level_status_col} = 'pending'
                AND status != 'cancelled'
                ORDER BY priority DESC, created_at ASC
                LIMIT ?
            ''', (level, limit))
            return [dict(row) for row in cursor.fetchall()]

    def get_features_for_testing_at_level(self, level: int, limit: int = MAX_PARALLEL_TASKS) -> List[Dict]:
        """Get features that need testing at a specific level."""
        level_status_col = {1: 'mvp_status', 2: 'enhanced_status', 3: 'advanced_status'}[level]
        level_test_count_col = {1: 'mvp_test_count', 2: 'enhanced_test_count', 3: 'advanced_test_count'}[level]

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(f'''
                SELECT * FROM improvements
                WHERE current_level = ? AND {level_status_col} = 'testing'
                AND {level_test_count_col} < ?
                AND status != 'cancelled'
                ORDER BY priority DESC
                LIMIT ?
            ''', (level, MAX_TEST_ATTEMPTS, limit))
            return [dict(row) for row in cursor.fetchall()]

    def get_stats_by_level(self) -> Dict:
        """Get statistics grouped by level."""
        with sqlite3.connect(self.db_path) as conn:
            stats = {}
            for level_name in ['MVP', 'Enhanced', 'Advanced']:
                level_num = {'MVP': 1, 'Enhanced': 2, 'Advanced': 3}[level_name]
                status_col = level_name.lower() + '_status'

                cursor = conn.execute(f'''
                    SELECT COUNT(*) FROM improvements WHERE {status_col} = 'completed'
                ''')
                completed = cursor.fetchone()[0]

                cursor = conn.execute(f'''
                    SELECT COUNT(*) FROM improvements WHERE {status_col} IN ('testing', 'approved')
                ''')
                in_progress = cursor.fetchone()[0]

                cursor = conn.execute(f'''
                    SELECT COUNT(*) FROM improvements WHERE {status_col} = 'pending' AND current_level = ?
                ''', (level_num,))
                pending = cursor.fetchone()[0]

                stats[level_name] = {
                    'completed': completed,
                    'in_progress': in_progress,
                    'pending': pending
                }

            return stats

    def get_recovery_stats(self) -> Dict:
        """Get statistics about task recovery and lifecycle."""
        with sqlite3.connect(self.db_path) as conn:
            stats = {}

            # Count stuck tasks
            cursor = conn.execute("SELECT COUNT(*) FROM improvements WHERE status = 'in_progress'")
            stats['stuck_count'] = cursor.fetchone()[0]

            # Average time in each status
            cursor = conn.execute('''
                SELECT status,
                       COUNT(*) as count,
                       AVG(CAST((julianday('now') - julianday(started_at)) * 24 * 60 AS INTEGER)) as avg_minutes
                FROM improvements
                WHERE started_at IS NOT NULL
                GROUP BY status
            ''')
            stats['status_duration'] = {row[0]: {'count': row[1], 'avg_minutes': row[2] or 0}
                                         for row in cursor.fetchall()}

            return stats
