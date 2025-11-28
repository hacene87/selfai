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

# Test retry limits
MAX_TEST_RETRIES = 3


class Database:
    """SQLite database manager for improvements with progressive complexity."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        # Ensure parent directory exists
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError) as e:
            raise RuntimeError(
                f"Cannot create database directory at {self.db_path.parent}: {e}. "
                f"Please check permissions or use a different path."
            ) from e
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
                    started_at TEXT,

                    -- Enhanced error handling
                    last_error TEXT,
                    execution_checkpoint TEXT,
                    plan_schema TEXT
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
                'ALTER TABLE improvements ADD COLUMN last_error TEXT',
                'ALTER TABLE improvements ADD COLUMN execution_checkpoint TEXT',
                'ALTER TABLE improvements ADD COLUMN plan_schema TEXT',
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
        """Add a new improvement (starts at MVP level).

        Args:
            title: Improvement title (required, min 1 char)
            description: Improvement description
            category: Category (default: 'general')
            priority: Priority 0-100 (default: 50)
            source: Source of improvement (default: 'ai_discovered')

        Returns:
            ID of created improvement

        Raises:
            ValueError: If title is empty or invalid
        """
        # Input validation
        if not title or not title.strip():
            raise ValueError("Title cannot be empty")
        if not isinstance(priority, int) or priority < 0 or priority > 100:
            raise ValueError(f"Priority must be 0-100, got {priority}")

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

    def get_all_in_progress(self) -> List[Dict]:
        """Get ALL in_progress tasks at once (avoids infinite loop bug)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('''
                SELECT * FROM improvements
                WHERE status = 'in_progress'
                ORDER BY started_at ASC
            ''')
            return [dict(row) for row in cursor.fetchall()]

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
        """Mark test as passed and auto-progress to next level.

        Features automatically progress through all 3 levels:
        - Level 1 (MVP) passed -> move to Level 2 (Enhanced)
        - Level 2 (Enhanced) passed -> move to Level 3 (Advanced)
        - Level 3 (Advanced) passed -> mark as truly completed
        """
        level_col = {1: 'mvp', 2: 'enhanced', 3: 'advanced'}[level]

        with sqlite3.connect(self.db_path) as conn:
            if level < 3:
                # Check if next level is unlocked
                next_level = level + 1
                if next_level == 2 and not self.check_level_unlock(2):
                    # Enhanced level locked - mark as completed for now
                    logger.info(f"Feature #{imp_id} completed MVP but Enhanced is locked. Marking completed.")
                    conn.execute(f'''
                        UPDATE improvements
                        SET {level_col}_test_status = 'passed',
                            {level_col}_test_output = ?,
                            status = 'completed'
                        WHERE id = ?
                    ''', (test_output, imp_id))
                elif next_level == 3 and not self.check_level_unlock(3):
                    # Advanced level locked - mark as completed for now
                    logger.info(f"Feature #{imp_id} completed Enhanced but Advanced is locked. Marking completed.")
                    conn.execute(f'''
                        UPDATE improvements
                        SET {level_col}_test_status = 'passed',
                            {level_col}_test_output = ?,
                            status = 'completed'
                        WHERE id = ?
                    ''', (test_output, imp_id))
                else:
                    # Auto-progress to next level
                    logger.info(f"Feature #{imp_id} passed level {level} - auto-progressing to level {next_level}")
                    conn.execute(f'''
                        UPDATE improvements
                        SET {level_col}_test_status = 'passed',
                            {level_col}_test_output = ?,
                            current_level = ?,
                            status = 'pending'
                        WHERE id = ?
                    ''', (test_output, next_level, imp_id))
            else:
                # Level 3 (Advanced) - truly completed
                logger.info(f"Feature #{imp_id} completed all 3 levels!")
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
        """Mark test as failed, go back to pending for retry (unless max retries reached)."""
        level_col = {1: 'mvp', 2: 'enhanced', 3: 'advanced'}[level]

        sanitized_output = self._sanitize_test_output(test_output)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('SELECT retry_count FROM improvements WHERE id = ?', (imp_id,))
            row = cursor.fetchone()
            if not row:
                logger.error(f"Cannot mark test failed for non-existent feature #{imp_id}")
                return False

            current_retry = row[0] or 0
            new_retry = current_retry + 1

            if new_retry >= MAX_TEST_RETRIES:
                logger.warning(f"Feature #{imp_id} reached max retries ({MAX_TEST_RETRIES}), marking as permanently failed")
                conn.execute(f'''
                    UPDATE improvements
                    SET {level_col}_test_status = 'failed',
                        {level_col}_test_output = ?,
                        status = 'failed',
                        retry_count = ?,
                        error = 'Max test retries reached'
                    WHERE id = ?
                ''', (sanitized_output, new_retry, imp_id))
            else:
                conn.execute(f'''
                    UPDATE improvements
                    SET {level_col}_test_status = 'failed',
                        {level_col}_test_output = ?,
                        status = 'pending',
                        retry_count = ?
                    WHERE id = ?
                ''', (sanitized_output, new_retry, imp_id))

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

    def get_success_fail_stats(self) -> Dict:
        """Get success/fail statistics for all tests.

        Returns:
            Dict with test success/fail counts, success rate, and retry statistics
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                stats = {
                    'mvp_passed': 0,
                    'mvp_failed': 0,
                    'enhanced_passed': 0,
                    'enhanced_failed': 0,
                    'advanced_passed': 0,
                    'advanced_failed': 0,
                    'total_passed': 0,
                    'total_failed': 0,
                    'success_rate': 0.0,
                    'total_retries': 0,
                    'avg_retries': 0.0
                }

                # Count passed and failed tests for each level
                for level in [1, 2, 3]:
                    level_col = {1: 'mvp', 2: 'enhanced', 3: 'advanced'}[level]
                    level_name = {1: 'mvp', 2: 'enhanced', 3: 'advanced'}[level]

                    # Count passed tests
                    cursor = conn.execute(
                        f"SELECT COUNT(*) FROM improvements WHERE {level_col}_test_status = 'passed'")
                    passed_count = cursor.fetchone()[0]
                    stats[f'{level_name}_passed'] = passed_count
                    stats['total_passed'] += passed_count

                    # Count failed tests (test marked as failed, regardless of final status)
                    # This counts all tests that failed at this level, even if feature later passed at higher level
                    cursor = conn.execute(
                        f"SELECT COUNT(*) FROM improvements WHERE {level_col}_test_status = 'failed'")
                    failed_count = cursor.fetchone()[0]
                    stats[f'{level_name}_failed'] = failed_count
                    stats['total_failed'] += failed_count

                # Calculate success rate
                total_tests = stats['total_passed'] + stats['total_failed']
                if total_tests > 0:
                    stats['success_rate'] = round((stats['total_passed'] / total_tests) * 100, 1)

                # Count total retries
                cursor = conn.execute("SELECT SUM(retry_count) FROM improvements")
                total_retries = cursor.fetchone()[0]
                stats['total_retries'] = total_retries if total_retries else 0

                # Calculate average retries per feature
                cursor = conn.execute("SELECT COUNT(*) FROM improvements")
                total_features = cursor.fetchone()[0]
                if total_features > 0:
                    stats['avg_retries'] = round(stats['total_retries'] / total_features, 1)

                return stats
        except Exception as e:
            logger.error(f"Failed to get success/fail stats from database: {e}")
            return {
                'mvp_passed': 0,
                'mvp_failed': 0,
                'enhanced_passed': 0,
                'enhanced_failed': 0,
                'advanced_passed': 0,
                'advanced_failed': 0,
                'total_passed': 0,
                'total_failed': 0,
                'success_rate': 0.0,
                'total_retries': 0,
                'avg_retries': 0.0
            }

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

    def _sanitize_test_output(self, output: str) -> str:
        """Sanitize test output for safe storage.

        Truncates very long outputs and handles special characters.
        """
        if not output:
            return ""

        if not isinstance(output, str):
            output = str(output)

        max_length = 10000
        if len(output) > max_length:
            output = output[:max_length] + f"\n... (truncated {len(output) - max_length} characters)"

        return output

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

    def get_improvements_by_files(self, file_paths: List[str]) -> List[Dict]:
        """Get improvements that modify any of the specified files.

        Args:
            file_paths: List of file paths to check for conflicts

        Returns:
            List of improvements that touch these files
        """
        if not file_paths:
            return []

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            improvements = []

            for file_path in file_paths:
                cursor = conn.execute('''
                    SELECT * FROM improvements
                    WHERE status IN ('in_progress', 'testing')
                    AND (
                        mvp_plan LIKE ?
                        OR enhanced_plan LIKE ?
                        OR advanced_plan LIKE ?
                    )
                ''', (f'%{file_path}%', f'%{file_path}%', f'%{file_path}%'))

                improvements.extend([dict(row) for row in cursor.fetchall()])

            seen_ids = set()
            unique_improvements = []
            for imp in improvements:
                if imp['id'] not in seen_ids:
                    seen_ids.add(imp['id'])
                    unique_improvements.append(imp)

            return unique_improvements

    def update_status(self, imp_id: int, new_status: str, error: str = None) -> bool:
        """Update status without validation (for simple state changes).

        Args:
            imp_id: Improvement ID
            new_status: New status to set
            error: Optional error message to store

        Returns:
            True if update succeeded
        """
        try:
            with sqlite3.connect(self.db_path, timeout=10.0) as conn:
                if error:
                    conn.execute(
                        'UPDATE improvements SET status = ?, error = ? WHERE id = ?',
                        (new_status, error, imp_id)
                    )
                else:
                    conn.execute(
                        'UPDATE improvements SET status = ? WHERE id = ?',
                        (new_status, imp_id)
                    )
                conn.commit()
                return True
        except sqlite3.Error as e:
            logger.error(f"Failed to update status for #{imp_id}: {e}")
            return False

    def update_status_with_validation(self, imp_id: int, new_status: str, max_retries: int = 3) -> bool:
        """Update status with transition validation and retry logic.

        Args:
            imp_id: Improvement ID
            new_status: New status to set
            max_retries: Maximum retry attempts

        Returns:
            True if update succeeded

        Raises:
            InvalidStatusTransitionError: If transition is invalid
        """
        from .validators import StatusTransitionValidator

        for attempt in range(max_retries):
            try:
                with sqlite3.connect(self.db_path, timeout=10.0) as conn:
                    cursor = conn.execute('SELECT status FROM improvements WHERE id = ?', (imp_id,))
                    row = cursor.fetchone()

                    if not row:
                        logger.error(f"Improvement #{imp_id} not found")
                        return False

                    current_status = row[0]
                    StatusTransitionValidator.validate_transition(current_status, new_status)

                    conn.execute('UPDATE improvements SET status = ? WHERE id = ?', (new_status, imp_id))
                    conn.commit()
                    return True

            except sqlite3.OperationalError as e:
                if 'locked' in str(e).lower() and attempt < max_retries - 1:
                    import time
                    wait_time = 0.5 * (2 ** attempt)
                    logger.warning(f"Database locked, retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})")
                    time.sleep(wait_time)
                    continue
                logger.error(f"Failed to update status after {attempt + 1} attempts: {e}")
                raise

        return False

    def save_execution_checkpoint(self, imp_id: int, checkpoint_data: str) -> bool:
        """Save execution checkpoint for partial recovery.

        Args:
            imp_id: Improvement ID
            checkpoint_data: Checkpoint data as JSON string

        Returns:
            True if save succeeded
        """
        try:
            with sqlite3.connect(self.db_path, timeout=10.0) as conn:
                conn.execute(
                    'UPDATE improvements SET execution_checkpoint = ? WHERE id = ?',
                    (checkpoint_data, imp_id)
                )
                conn.commit()
                return True
        except sqlite3.Error as e:
            logger.error(f"Failed to save execution checkpoint for #{imp_id}: {e}")
            return False

    def get_execution_checkpoint(self, imp_id: int) -> Optional[str]:
        """Get execution checkpoint for an improvement.

        Args:
            imp_id: Improvement ID

        Returns:
            Checkpoint data as JSON string or None
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    'SELECT execution_checkpoint FROM improvements WHERE id = ?',
                    (imp_id,)
                )
                row = cursor.fetchone()
                return row[0] if row and row[0] else None
        except sqlite3.Error as e:
            logger.error(f"Failed to get execution checkpoint for #{imp_id}: {e}")
            return None

    def can_test_feature(self, feature_id: int) -> tuple[bool, str]:
        """Check if a feature can be tested.

        Args:
            feature_id: Feature ID to check

        Returns:
            Tuple of (can_test: bool, reason: str)
        """
        try:
            feature = self.get_by_id(feature_id)
            if not feature:
                return False, "Feature not found"

            if feature['status'] not in ('testing', 'in_progress'):
                return False, f"Feature status is '{feature['status']}', must be 'testing' or 'in_progress'"

            retry_count = feature.get('retry_count', 0)
            if retry_count >= MAX_TEST_RETRIES:
                return False, f"Max retries exceeded ({retry_count}/{MAX_TEST_RETRIES})"

            return True, "Feature can be tested"

        except Exception as e:
            logger.error(f"Error checking if feature #{feature_id} can be tested: {e}")
            return False, f"Error: {e}"
