"""SQLite database for tracking improvements with 3-level progression."""
import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import logging

logger = logging.getLogger('selfai')

# Level names
LEVEL_NAMES = {1: 'MVP', 2: 'Enhanced', 3: 'Advanced'}


class Database:
    """SQLite database manager for improvements with progressive complexity."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        # Ensure parent directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Initialize database schema for 3-level progression."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('''
                CREATE TABLE IF NOT EXISTS improvements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    description TEXT,
                    category TEXT DEFAULT 'general',
                    priority INTEGER DEFAULT 50,
                    source TEXT DEFAULT 'ai_discovered',
                    created_at TEXT,

                    -- Current state
                    current_level INTEGER DEFAULT 1,
                    status TEXT DEFAULT 'pending',

                    -- Plans for each level (JSON)
                    mvp_plan TEXT,
                    enhanced_plan TEXT,
                    advanced_plan TEXT,

                    -- Outputs for each level
                    mvp_output TEXT,
                    enhanced_output TEXT,
                    advanced_output TEXT,

                    -- Test status for each level: pending, passed, failed
                    mvp_test_status TEXT DEFAULT 'pending',
                    enhanced_test_status TEXT DEFAULT 'pending',
                    advanced_test_status TEXT DEFAULT 'pending',

                    -- Test outputs
                    mvp_test_output TEXT,
                    enhanced_test_output TEXT,
                    advanced_test_output TEXT,

                    -- Timestamps
                    mvp_completed_at TEXT,
                    enhanced_completed_at TEXT,
                    advanced_completed_at TEXT,

<<<<<<< HEAD
                    -- Durations in seconds
=======
                    -- Duration tracking (in seconds)
>>>>>>> feature/67-dashboard-estimated-time-remaining
                    mvp_duration INTEGER,
                    enhanced_duration INTEGER,
                    advanced_duration INTEGER,

                    -- Error tracking
                    error TEXT,
                    retry_count INTEGER DEFAULT 0,
                    started_at TEXT
                )
                ''')

                # Migrations for existing databases
                migrations = [
                'ALTER TABLE improvements ADD COLUMN current_level INTEGER DEFAULT 1',
                'ALTER TABLE improvements ADD COLUMN mvp_plan TEXT',
                'ALTER TABLE improvements ADD COLUMN enhanced_plan TEXT',
                'ALTER TABLE improvements ADD COLUMN advanced_plan TEXT',
                'ALTER TABLE improvements ADD COLUMN mvp_output TEXT',
                'ALTER TABLE improvements ADD COLUMN enhanced_output TEXT',
                'ALTER TABLE improvements ADD COLUMN advanced_output TEXT',
                'ALTER TABLE improvements ADD COLUMN mvp_test_status TEXT DEFAULT "pending"',
                'ALTER TABLE improvements ADD COLUMN enhanced_test_status TEXT DEFAULT "pending"',
                'ALTER TABLE improvements ADD COLUMN advanced_test_status TEXT DEFAULT "pending"',
                'ALTER TABLE improvements ADD COLUMN mvp_test_output TEXT',
                'ALTER TABLE improvements ADD COLUMN enhanced_test_output TEXT',
                'ALTER TABLE improvements ADD COLUMN advanced_test_output TEXT',
                'ALTER TABLE improvements ADD COLUMN mvp_completed_at TEXT',
                'ALTER TABLE improvements ADD COLUMN enhanced_completed_at TEXT',
                'ALTER TABLE improvements ADD COLUMN advanced_completed_at TEXT',
                'ALTER TABLE improvements ADD COLUMN mvp_duration INTEGER',
                'ALTER TABLE improvements ADD COLUMN enhanced_duration INTEGER',
                'ALTER TABLE improvements ADD COLUMN advanced_duration INTEGER',
<<<<<<< HEAD
                ]
                for migration in migrations:
                    try:
                        conn.execute(migration)
                    except sqlite3.OperationalError:
                        pass
=======
            ]
            for migration in migrations:
                try:
                    conn.execute(migration)
                except sqlite3.OperationalError:
                    pass
>>>>>>> feature/67-dashboard-estimated-time-remaining

                conn.execute('CREATE INDEX IF NOT EXISTS idx_status ON improvements(status)')
                conn.execute('CREATE INDEX IF NOT EXISTS idx_priority ON improvements(priority)')
                conn.execute('CREATE INDEX IF NOT EXISTS idx_current_level ON improvements(current_level)')
                conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Database connection failed: {e}")
            raise RuntimeError(f"Failed to initialize database at {self.db_path}: {e}") from e

    def add(self, title: str, description: str, category: str = 'general',
            priority: int = 50, source: str = 'ai_discovered') -> int:
        """Add a new improvement (starts at MVP level)."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                INSERT INTO improvements (title, description, category, priority, source, current_level, created_at)
                VALUES (?, ?, ?, ?, ?, 1, ?)
            ''', (title, description, category, priority, source, datetime.now().isoformat()))
            conn.commit()
            logger.info(f"Added improvement #{cursor.lastrowid}: {title}")
            return cursor.lastrowid

    def get_next_in_progress(self) -> Optional[Dict]:
        """Get stuck in_progress task."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('''
                SELECT * FROM improvements
                WHERE status = 'in_progress'
                ORDER BY started_at ASC
                LIMIT 1
            ''')
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_next_pending(self) -> Optional[Dict]:
        """Get next pending improvement (high priority, oldest first)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('''
                SELECT * FROM improvements
                WHERE status = 'pending'
                ORDER BY priority DESC, created_at ASC
                LIMIT 1
            ''')
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_next_needs_testing(self) -> Optional[Dict]:
        """Get next improvement that needs testing at current level."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            # Find improvements where current level is completed but not tested
            cursor = conn.execute('''
                SELECT * FROM improvements
                WHERE status = 'testing'
                ORDER BY started_at ASC
                LIMIT 1
            ''')
            row = cursor.fetchone()
            return dict(row) if row else None

    def mark_in_progress(self, imp_id: int) -> bool:
        """Mark improvement as in progress."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                UPDATE improvements SET status = 'in_progress', started_at = ?
                WHERE id = ?
            ''', (datetime.now().isoformat(), imp_id))
            conn.commit()
            return True

    def mark_level_completed(self, imp_id: int, level: int, output: str = '') -> bool:
        """Mark current level as completed, move to testing."""
        level_col = {1: 'mvp', 2: 'enhanced', 3: 'advanced'}[level]
        with sqlite3.connect(self.db_path) as conn:
<<<<<<< HEAD
            # Get started_at to calculate duration
=======
            # Get started_at timestamp to calculate duration
>>>>>>> feature/67-dashboard-estimated-time-remaining
            cursor = conn.execute('SELECT started_at FROM improvements WHERE id = ?', (imp_id,))
            row = cursor.fetchone()
            duration = None
            if row and row[0]:
<<<<<<< HEAD
                try:
                    started_at = datetime.fromisoformat(row[0])
                    duration = int((datetime.now() - started_at).total_seconds())
                except (ValueError, AttributeError):
                    pass
=======
                started_at = datetime.fromisoformat(row[0])
                duration = int((datetime.now() - started_at).total_seconds())
>>>>>>> feature/67-dashboard-estimated-time-remaining

            conn.execute(f'''
                UPDATE improvements
                SET status = 'testing',
                    {level_col}_output = ?,
                    {level_col}_completed_at = ?,
                    {level_col}_duration = ?
                WHERE id = ?
            ''', (output, datetime.now().isoformat(), duration, imp_id))
            conn.commit()
            return True

    def mark_test_passed(self, imp_id: int, level: int, test_output: str = '') -> bool:
        """Mark test as passed - feature is completed at this level.

        Features are considered complete after passing tests at ANY level.
        Higher levels (Enhanced, Advanced) are optional enhancements.
        """
        level_col = {1: 'mvp', 2: 'enhanced', 3: 'advanced'}[level]
        with sqlite3.connect(self.db_path) as conn:
            # Mark as completed at current level - no forced progression
            conn.execute(f'''
                UPDATE improvements
                SET {level_col}_test_status = 'passed',
                    {level_col}_test_output = ?,
                    status = 'completed'
                WHERE id = ?
            ''', (test_output, imp_id))
            conn.commit()
            return True

    def enhance_feature(self, imp_id: int) -> bool:
        """Optionally enhance a completed feature to the next level.

        Call this to move a completed feature back to pending
        for enhancement to the next level.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                'SELECT current_level, status FROM improvements WHERE id = ?', (imp_id,))
            row = cursor.fetchone()
            if not row or row[1] != 'completed':
                return False

            current_level = row[0]
            if current_level >= 3:
                return False  # Already at max level

            conn.execute('''
                UPDATE improvements
                SET current_level = ?,
                    status = 'pending'
                WHERE id = ?
            ''', (current_level + 1, imp_id))
            conn.commit()
            return True

    def mark_test_failed(self, imp_id: int, level: int, test_output: str = '') -> bool:
        """Mark test as failed, go back to pending for retry."""
        level_col = {1: 'mvp', 2: 'enhanced', 3: 'advanced'}[level]
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(f'''
                UPDATE improvements
                SET {level_col}_test_status = 'failed',
                    {level_col}_test_output = ?,
                    status = 'pending',
                    retry_count = retry_count + 1
                WHERE id = ?
            ''', (test_output, imp_id))
            conn.commit()
            return True

    def mark_failed(self, imp_id: int, error: str) -> bool:
        """Mark improvement as failed, set back to pending for retry."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                UPDATE improvements
                SET status = 'pending', error = ?, retry_count = retry_count + 1
                WHERE id = ?
            ''', (error, imp_id))
            conn.commit()
            return True

    def save_plan(self, imp_id: int, level: int, plan: str) -> bool:
        """Save the execution plan for a specific level."""
        level_col = {1: 'mvp', 2: 'enhanced', 3: 'advanced'}[level]
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(f'UPDATE improvements SET {level_col}_plan = ? WHERE id = ?', (plan, imp_id))
            conn.commit()
            return True

    def get_plan(self, imp_id: int, level: int) -> Optional[str]:
        """Get the plan for a specific level."""
        level_col = {1: 'mvp', 2: 'enhanced', 3: 'advanced'}[level]
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(f'SELECT {level_col}_plan FROM improvements WHERE id = ?', (imp_id,))
            row = cursor.fetchone()
            return row[0] if row and row[0] else None

    def get_all(self) -> List[Dict]:
        """Get all improvements ordered by ID descending."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('SELECT * FROM improvements ORDER BY id DESC')
            return [dict(row) for row in cursor.fetchall()]

    def get_stats(self) -> Dict:
        """Get statistics."""
        with sqlite3.connect(self.db_path) as conn:
            stats = {}
            for status in ['pending', 'in_progress', 'testing', 'completed']:
                cursor = conn.execute("SELECT COUNT(*) FROM improvements WHERE status = ?", (status,))
                stats[status] = cursor.fetchone()[0]
            cursor = conn.execute("SELECT COUNT(*) FROM improvements")
            stats['total'] = cursor.fetchone()[0]
            return stats

    def get_level_stats(self) -> Dict:
        """Get statistics by current level."""
        with sqlite3.connect(self.db_path) as conn:
            stats = {}
            for level in [1, 2, 3]:
                cursor = conn.execute(
                    "SELECT COUNT(*) FROM improvements WHERE current_level = ? AND status != 'completed'",
                    (level,))
                stats[level] = {'in_progress': cursor.fetchone()[0]}

                # Count completed at each level
                level_col = {1: 'mvp', 2: 'enhanced', 3: 'advanced'}[level]
                cursor = conn.execute(
                    f"SELECT COUNT(*) FROM improvements WHERE {level_col}_test_status = 'passed'")
                stats[level]['passed'] = cursor.fetchone()[0]
            return stats

    def get_average_duration_by_level(self) -> Dict[int, Optional[float]]:
        """Get average duration in seconds for each level from completed tasks."""
        with sqlite3.connect(self.db_path) as conn:
            averages = {}
            for level in [1, 2, 3]:
                level_col = {1: 'mvp', 2: 'enhanced', 3: 'advanced'}[level]
                cursor = conn.execute(
                    f"SELECT AVG({level_col}_duration) FROM improvements WHERE {level_col}_duration IS NOT NULL")
                result = cursor.fetchone()[0]
                averages[level] = result if result else None
            return averages

    def get_tasks_with_time_estimates(self) -> List[Dict]:
        """Get all tasks with estimated time remaining for in-progress tasks.

        Returns list of tasks with additional 'estimated_remaining' field (in seconds) for active tasks.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('SELECT * FROM improvements ORDER BY id DESC')
            tasks = [dict(row) for row in cursor.fetchall()]

            # Get average durations
            averages = self.get_average_duration_by_level()

            # Calculate estimated remaining time for in-progress tasks
            now = datetime.now()
            for task in tasks:
                task['estimated_remaining'] = None
                if task['status'] in ('in_progress', 'testing') and task['started_at']:
                    try:
                        started_at = datetime.fromisoformat(task['started_at'])
                        elapsed = (now - started_at).total_seconds()
                        level = task['current_level']
                        avg_duration = averages.get(level)

                        if avg_duration:
                            remaining = avg_duration - elapsed
                            task['estimated_remaining'] = max(0, remaining)  # Don't show negative
                    except (ValueError, AttributeError):
                        pass

            return tasks

    def exists(self, title: str) -> bool:
        """Check if improvement with title already exists."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT 1 FROM improvements WHERE title = ?", (title,))
            return cursor.fetchone() is not None

    def get_completed_features(self) -> List[str]:
        """Get list of feature titles that have at least MVP completed."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT title FROM improvements WHERE mvp_test_status = 'passed'")
            return [row[0] for row in cursor.fetchall()]

    def get_by_id(self, imp_id: int) -> Optional[Dict]:
        """Get a single improvement by ID."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('SELECT * FROM improvements WHERE id = ?', (imp_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_success_fail_stats(self) -> Dict:
        """Get success/fail statistics and retry metrics.

        Returns:
            Dict with:
            - total_passed: Total tests passed across all levels
            - total_failed: Total tests failed across all levels
            - success_rate: Percentage of passed tests (0-100)
            - total_retries: Sum of all retry counts
            - avg_retries: Average retries per feature
            - mvp_passed: Count of MVP tests passed
            - mvp_failed: Count of MVP tests failed
            - enhanced_passed: Count of Enhanced tests passed
            - enhanced_failed: Count of Enhanced tests failed
            - advanced_passed: Count of Advanced tests passed
            - advanced_failed: Count of Advanced tests failed
        """
        with sqlite3.connect(self.db_path) as conn:
            stats = {}

            # Count passed/failed tests for each level
            for level, level_col in [(1, 'mvp'), (2, 'enhanced'), (3, 'advanced')]:
                cursor = conn.execute(
                    f"SELECT COUNT(*) FROM improvements WHERE {level_col}_test_status = 'passed'")
                stats[f'{level_col}_passed'] = cursor.fetchone()[0]

                cursor = conn.execute(
                    f"SELECT COUNT(*) FROM improvements WHERE {level_col}_test_status = 'failed'")
                stats[f'{level_col}_failed'] = cursor.fetchone()[0]

            # Total passed/failed across all levels
            stats['total_passed'] = (stats['mvp_passed'] + stats['enhanced_passed'] +
                                    stats['advanced_passed'])
            stats['total_failed'] = (stats['mvp_failed'] + stats['enhanced_failed'] +
                                    stats['advanced_failed'])

            # Success rate
            total_tests = stats['total_passed'] + stats['total_failed']
            if total_tests > 0:
                stats['success_rate'] = round((stats['total_passed'] / total_tests) * 100, 1)
            else:
                stats['success_rate'] = 0.0

            # Retry metrics
            cursor = conn.execute("SELECT SUM(retry_count), COUNT(*) FROM improvements")
            row = cursor.fetchone()
            stats['total_retries'] = row[0] if row[0] else 0
            feature_count = row[1] if row[1] else 0
            stats['avg_retries'] = round(stats['total_retries'] / feature_count, 1) if feature_count > 0 else 0.0

            return stats

    def progress_all_to_level(self, next_level: int) -> int:
        """Progress all completed features to the next level.

        Moves all features with status='completed' to:
        - current_level = next_level
        - status = 'pending'

        Returns the number of features moved.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                UPDATE improvements
                SET current_level = ?, status = 'pending'
                WHERE status = 'completed'
            ''', (next_level,))
            conn.commit()
            return cursor.rowcount

    def get_all_at_level(self, level: int) -> List[Dict]:
        """Get all features currently at a specific level."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                'SELECT * FROM improvements WHERE current_level = ?', (level,))
            return [dict(row) for row in cursor.fetchall()]

    def get_average_duration_by_level(self) -> Dict[int, Optional[float]]:
        """Get average completion duration for each level in seconds."""
        with sqlite3.connect(self.db_path) as conn:
            averages = {}
            for level in [1, 2, 3]:
                level_col = {1: 'mvp', 2: 'enhanced', 3: 'advanced'}[level]
                cursor = conn.execute(
                    f'SELECT AVG({level_col}_duration) FROM improvements WHERE {level_col}_duration IS NOT NULL')
                result = cursor.fetchone()[0]
                averages[level] = result if result else None
            return averages

    def get_tasks_with_time_estimates(self) -> List[Dict]:
        """Get all tasks with estimated time remaining for in-progress tasks."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('SELECT * FROM improvements ORDER BY id DESC')
            tasks = [dict(row) for row in cursor.fetchall()]

            # Get average durations for each level
            avg_durations = self.get_average_duration_by_level()

            # Calculate time estimates for each task
            for task in tasks:
                task['estimated_remaining'] = None
                if task['status'] in ['in_progress', 'testing'] and task['started_at']:
                    level = task['current_level']
                    avg_duration = avg_durations.get(level)
                    if avg_duration:
                        started_at = datetime.fromisoformat(task['started_at'])
                        elapsed = (datetime.now() - started_at).total_seconds()
                        remaining = avg_duration - elapsed
                        task['estimated_remaining'] = max(0, remaining)  # Don't show negative time

            return tasks
