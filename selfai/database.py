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

                    -- Durations in seconds
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
                ]
                for migration in migrations:
                    try:
                        conn.execute(migration)
                    except sqlite3.OperationalError:
                        pass

                conn.execute('''
                CREATE TABLE IF NOT EXISTS unlock_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    level INTEGER NOT NULL,
                    feature_id INTEGER,
                    unlocked_at TEXT NOT NULL,
                    UNIQUE(level)
                )
                ''')

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
            # Get started_at to calculate duration
            cursor = conn.execute('SELECT started_at FROM improvements WHERE id = ?', (imp_id,))
            row = cursor.fetchone()
            duration = None
            if row and row[0]:
                try:
                    started_at = datetime.fromisoformat(row[0])
                    duration = int((datetime.now() - started_at).total_seconds())
                except (ValueError, AttributeError):
                    pass

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
        """Get statistics.

        Returns:
            Dict with counts for each status, never None
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                stats = {}
                for status in ['pending', 'in_progress', 'testing', 'completed']:
                    cursor = conn.execute("SELECT COUNT(*) FROM improvements WHERE status = ?", (status,))
                    stats[status] = cursor.fetchone()[0]
                cursor = conn.execute("SELECT COUNT(*) FROM improvements")
                stats['total'] = cursor.fetchone()[0]
                return stats
        except Exception as e:
            logger.error(f"Failed to get stats from database: {e}")
            return {'pending': 0, 'in_progress': 0, 'testing': 0, 'completed': 0, 'total': 0}

    def get_level_stats(self) -> Dict:
        """Get statistics by current level.

        Returns:
            Dict with level statistics, never None
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                stats = {}
                for level in [1, 2, 3]:
                    cursor = conn.execute(
                        "SELECT COUNT(*) FROM improvements WHERE current_level = ? AND status != 'completed'",
                        (level,))
                    stats[level] = {'in_progress': cursor.fetchone()[0]}

                    level_col = {1: 'mvp', 2: 'enhanced', 3: 'advanced'}[level]
                    cursor = conn.execute(
                        f"SELECT COUNT(*) FROM improvements WHERE {level_col}_test_status = 'passed'")
                    stats[level]['passed'] = cursor.fetchone()[0]
                return stats
        except Exception as e:
            logger.error(f"Failed to get level stats from database: {e}")
            return {}

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

        Returns:
            List of tasks with 'estimated_remaining' field, never None
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute('SELECT * FROM improvements ORDER BY id DESC')
                tasks = [dict(row) for row in cursor.fetchall()]

                averages = self.get_average_duration_by_level()

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
                                task['estimated_remaining'] = max(0, remaining)
                        except (ValueError, AttributeError):
                            pass

                return tasks
        except Exception as e:
            logger.error(f"Failed to get tasks with time estimates from database: {e}")
            return []

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

    def check_level_unlock(self, level: int) -> bool:
        """Check if a level is unlocked based on previous level completions.

        Args:
            level: The level to check (2=Enhanced, 3=Advanced)

        Returns:
            True if level is unlocked, False otherwise

        Raises:
            ValueError: If level is invalid (must be 2 or 3)
        """
        if level not in (2, 3):
            raise ValueError(f"Invalid level: {level}. Only Enhanced (2) and Advanced (3) can be locked.")

        thresholds = {2: 5, 3: 10}
        threshold = thresholds[level]
        prev_level_col = {2: 'mvp', 3: 'enhanced'}[level]

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    f"SELECT COUNT(*) FROM improvements WHERE {prev_level_col}_test_status = 'passed'")
                count = cursor.fetchone()[0]

                if count is None or count < 0:
                    logger.warning(f"Invalid count ({count}) when checking level {level} unlock")
                    return False

                return count >= threshold
        except sqlite3.Error as e:
            logger.error(f"Database error checking level {level} unlock: {e}")
            return False

    def get_unlock_progress(self, level: int) -> tuple[int, int]:
        """Get progress toward unlocking a level.

        Args:
            level: The level to check (2=Enhanced, 3=Advanced)

        Returns:
            Tuple of (current_count, required_count)

        Raises:
            ValueError: If level is invalid
        """
        if level not in (2, 3):
            raise ValueError(f"Invalid level: {level}. Only Enhanced (2) and Advanced (3) can be locked.")

        thresholds = {2: 5, 3: 10}
        threshold = thresholds[level]
        prev_level_col = {2: 'mvp', 3: 'enhanced'}[level]

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    f"SELECT COUNT(*) FROM improvements WHERE {prev_level_col}_test_status = 'passed'")
                count = cursor.fetchone()[0]

                if count is None or count < 0:
                    logger.warning(f"Invalid count ({count}) for level {level} progress")
                    return (0, threshold)

                return (count, threshold)
        except sqlite3.Error as e:
            logger.error(f"Database error getting level {level} progress: {e}")
            return (0, threshold)

    def record_unlock_event(self, level: int, feature_id: Optional[int] = None) -> bool:
        """Record when a level is unlocked.

        Args:
            level: The level that was unlocked (2=Enhanced, 3=Advanced)
            feature_id: Optional ID of the feature that triggered the unlock

        Returns:
            True if event was recorded, False if already recorded or error

        Raises:
            ValueError: If level is invalid
        """
        if level not in (2, 3):
            raise ValueError(f"Invalid level: {level}. Only Enhanced (2) and Advanced (3) can be locked.")

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    'SELECT id FROM unlock_events WHERE level = ?', (level,))
                if cursor.fetchone():
                    logger.debug(f"Level {level} unlock already recorded")
                    return False

                conn.execute(
                    'INSERT INTO unlock_events (level, feature_id, unlocked_at) VALUES (?, ?, ?)',
                    (level, feature_id, datetime.now().isoformat()))
                conn.commit()
                logger.info(f"Level {level} ({LEVEL_NAMES[level]}) unlocked!")
                return True
        except sqlite3.IntegrityError:
            logger.debug(f"Level {level} unlock already recorded (integrity constraint)")
            return False
        except sqlite3.Error as e:
            logger.error(f"Database error recording level {level} unlock: {e}")
            return False
