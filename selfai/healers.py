"""
Self-healing action executors and knowledge base for SelfAI monitoring system.
Implements MAPE-K loop pattern (Monitor, Analyze, Plan, Execute, Knowledge).
"""

import json
import logging
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, asdict

from .monitors import DetectedError

logger = logging.getLogger(__name__)


@dataclass
class Diagnosis:
    """Represents a diagnosis of a detected error."""
    error: DetectedError
    root_cause: str
    recommended_action: str
    confidence: float  # 0.0 to 1.0
    context: Dict = None

    def __post_init__(self):
        if self.context is None:
            self.context = {}


@dataclass
class HealingResult:
    """Represents the result of a healing action."""
    success: bool
    message: str
    action_taken: Optional[str] = None
    context: Dict = None

    def __post_init__(self):
        if self.context is None:
            self.context = {}


class ErrorAnalyzer:
    """Analyzes detected errors and determines root cause (MAPE-K Analyze phase)."""

    def __init__(self, knowledge_base=None):
        """
        Initialize error analyzer.

        Args:
            knowledge_base: KnowledgeBase instance for historical data
        """
        self.knowledge_base = knowledge_base

    def diagnose(self, error: DetectedError) -> Diagnosis:
        """
        Diagnose root cause based on error pattern and context.

        Args:
            error: Detected error to diagnose

        Returns:
            Diagnosis with root cause and recommended action
        """
        # Check knowledge base for similar issues
        similar_cases = []
        if self.knowledge_base:
            similar_cases = self.knowledge_base.find_similar(error)

        # Determine root cause based on error type
        root_cause = self._determine_root_cause(error)
        recommended_action = self._get_action(error)
        confidence = self._calculate_confidence(error, similar_cases)

        diagnosis = Diagnosis(
            error=error,
            root_cause=root_cause,
            recommended_action=recommended_action,
            confidence=confidence,
            context={'similar_cases_count': len(similar_cases)}
        )

        logger.debug(f"Diagnosed '{error.pattern_type}': {root_cause} (confidence: {confidence:.2f})")
        return diagnosis

    def _determine_root_cause(self, error: DetectedError) -> str:
        """Determine root cause based on error pattern."""
        root_causes = {
            'database_locked': 'Database lock contention - multiple processes accessing database',
            'too_many_files': 'File descriptor exhaustion - too many files open simultaneously',
            'worktree_conflict': 'Git worktree conflict or stale worktree references',
            'lock_file_stuck': 'Stale lock file from crashed or terminated process',
            'worker_failure': 'Worker thread or process failure in executor',
            'timeout': 'Operation exceeded allowed time limit',
            'git_error': 'Git operation failure',
        }
        return root_causes.get(error.pattern_type, 'Unknown error pattern')

    def _get_action(self, error: DetectedError) -> str:
        """Get recommended action for error type."""
        actions = {
            'database_locked': 'Enable WAL mode, increase timeout, remove stale locks',
            'too_many_files': 'Force garbage collection, close orphaned file handles',
            'worktree_conflict': 'Prune worktrees, clean orphaned directories',
            'lock_file_stuck': 'Verify process status and remove stale lock file',
            'worker_failure': 'Restart worker pool',
            'timeout': 'Increase timeout or cancel operation',
            'git_error': 'Retry git operation with cleanup',
        }
        return actions.get(error.pattern_type, 'Manual investigation required')

    def _calculate_confidence(self, error: DetectedError, similar_cases: List[Dict]) -> float:
        """
        Calculate confidence score for diagnosis.

        Args:
            error: Detected error
            similar_cases: Similar past cases from knowledge base

        Returns:
            Confidence score from 0.0 to 1.0
        """
        # Base confidence on error pattern
        base_confidence = {
            'database_locked': 0.8,
            'too_many_files': 0.7,
            'worktree_conflict': 0.75,
            'lock_file_stuck': 0.85,
            'worker_failure': 0.6,
            'timeout': 0.5,
            'git_error': 0.5,
        }

        confidence = base_confidence.get(error.pattern_type, 0.3)

        # Increase confidence if we have successful similar cases
        if similar_cases:
            success_rate = sum(1 for case in similar_cases if case.get('success', False)) / len(similar_cases)
            confidence = min(0.95, confidence + (success_rate * 0.2))

        return confidence


class SelfHealingExecutor:
    """Executes remediation actions (MAPE-K Execute phase)."""

    def __init__(self, repo_path: Path, db_path: Optional[Path] = None):
        """
        Initialize self-healing executor.

        Args:
            repo_path: Path to repository root
            db_path: Path to database file (optional)
        """
        self.repo_path = repo_path
        self.db_path = db_path
        self.actions = self._register_actions()

    def _register_actions(self) -> Dict[str, Callable]:
        """Register healing actions for each error type."""
        return {
            'database_locked': self._fix_database_lock,
            'too_many_files': self._fix_file_descriptors,
            'worktree_conflict': self._fix_worktree_conflict,
            'lock_file_stuck': self._fix_stuck_lock,
            'worker_failure': self._restart_workers,
            'timeout': self._handle_timeout,
        }

    def execute(self, diagnosis: Diagnosis) -> HealingResult:
        """
        Execute healing action based on diagnosis.

        Args:
            diagnosis: Diagnosis containing error and recommended action

        Returns:
            HealingResult indicating success or failure
        """
        action = self.actions.get(diagnosis.error.pattern_type)
        if not action:
            return HealingResult(
                success=False,
                message=f'No healing action defined for {diagnosis.error.pattern_type}'
            )

        try:
            logger.info(f'Attempting to heal: {diagnosis.error.pattern_type}')
            result = action(diagnosis)
            result.action_taken = diagnosis.recommended_action
            return result
        except Exception as e:
            logger.error(f'Healing failed for {diagnosis.error.pattern_type}: {e}')
            return HealingResult(
                success=False,
                message=str(e),
                action_taken=diagnosis.recommended_action
            )

    def _fix_database_lock(self, diagnosis: Diagnosis) -> HealingResult:
        """Fix database lock issues."""
        try:
            # Find database file
            db_path = self.db_path
            if not db_path:
                db_path = self.repo_path / '.selfai_data' / 'data' / 'improvements.db'

            if not db_path.exists():
                return HealingResult(success=False, message='Database file not found')

            # Enable WAL mode if not already
            with sqlite3.connect(str(db_path), timeout=30.0) as conn:
                conn.execute('PRAGMA journal_mode=WAL')
                conn.execute('PRAGMA busy_timeout=30000')  # 30 second timeout
                logger.info('Enabled WAL mode and increased timeout')

            # Check for stale lock files
            stale_locks_removed = 0
            for lock_pattern in ['*-journal', '*-wal', '*-shm']:
                lock_files = list(db_path.parent.glob(lock_pattern))
                for lock_file in lock_files:
                    if self._is_stale_lock(lock_file):
                        lock_file.unlink()
                        logger.info(f'Removed stale lock: {lock_file}')
                        stale_locks_removed += 1

            return HealingResult(
                success=True,
                message=f'Database lock fixed, removed {stale_locks_removed} stale locks',
                context={'stale_locks_removed': stale_locks_removed}
            )
        except Exception as e:
            return HealingResult(success=False, message=f'Failed to fix database lock: {e}')

    def _fix_file_descriptors(self, diagnosis: Diagnosis) -> HealingResult:
        """Handle too many open files error."""
        try:
            # Force garbage collection
            import gc
            gc.collect()

            # Try to get file descriptor info
            try:
                import psutil
                process = psutil.Process()
                open_files_before = len(process.open_files())
                gc.collect()
                open_files_after = len(process.open_files())

                return HealingResult(
                    success=True,
                    message=f'Forced garbage collection. Open files: {open_files_before} -> {open_files_after}',
                    context={'open_files_before': open_files_before, 'open_files_after': open_files_after}
                )
            except ImportError:
                return HealingResult(
                    success=True,
                    message='Forced garbage collection (psutil not available for detailed stats)'
                )
        except Exception as e:
            return HealingResult(success=False, message=f'Failed to fix file descriptors: {e}')

    def _fix_worktree_conflict(self, diagnosis: Diagnosis) -> HealingResult:
        """Clean up worktree conflicts."""
        try:
            # Run git worktree prune
            result = subprocess.run(
                ['git', 'worktree', 'prune'],
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode != 0:
                logger.warning(f'Git worktree prune failed: {result.stderr}')

            # Check for orphaned worktree directories
            worktree_dir = self.repo_path / '.selfai_data' / 'worktrees'
            orphaned_removed = 0

            if worktree_dir.exists():
                for wt_path in worktree_dir.iterdir():
                    if wt_path.is_dir() and not (wt_path / '.git').exists():
                        try:
                            shutil.rmtree(wt_path)
                            logger.info(f'Removed orphaned worktree: {wt_path}')
                            orphaned_removed += 1
                        except Exception as e:
                            logger.warning(f'Failed to remove orphaned worktree {wt_path}: {e}')

            return HealingResult(
                success=True,
                message=f'Worktree conflicts resolved, removed {orphaned_removed} orphaned worktrees',
                context={'orphaned_removed': orphaned_removed}
            )
        except Exception as e:
            return HealingResult(success=False, message=f'Failed to fix worktree conflicts: {e}')

    def _fix_stuck_lock(self, diagnosis: Diagnosis) -> HealingResult:
        """Remove stuck lock files."""
        try:
            lock_file = self.repo_path / '.selfai_data' / 'runner.lock'

            if not lock_file.exists():
                return HealingResult(success=False, message='Lock file does not exist')

            # Check if PID is still running
            try:
                pid_str = lock_file.read_text().strip()
                pid = int(pid_str)

                # Check if process exists
                try:
                    import psutil
                    if psutil.pid_exists(pid):
                        return HealingResult(
                            success=False,
                            message=f'Lock file is valid (process {pid} is running)'
                        )
                except ImportError:
                    # Fallback: check using os.kill
                    try:
                        os.kill(pid, 0)
                        return HealingResult(
                            success=False,
                            message=f'Lock file is valid (process {pid} is running)'
                        )
                    except OSError:
                        pass  # Process doesn't exist

                # Process doesn't exist, remove lock
                lock_file.unlink()
                return HealingResult(
                    success=True,
                    message=f'Removed stale lock file (PID {pid} not running)'
                )

            except (ValueError, FileNotFoundError):
                # Invalid lock file content
                lock_file.unlink()
                return HealingResult(success=True, message='Removed invalid lock file')

        except Exception as e:
            return HealingResult(success=False, message=f'Failed to fix stuck lock: {e}')

    def _restart_workers(self, diagnosis: Diagnosis) -> HealingResult:
        """Handle worker failure."""
        # This is a placeholder - actual implementation would depend on worker architecture
        return HealingResult(
            success=False,
            message='Worker restart not implemented - requires manual intervention'
        )

    def _handle_timeout(self, diagnosis: Diagnosis) -> HealingResult:
        """Handle timeout errors."""
        # This is a placeholder - actual implementation would depend on what timed out
        return HealingResult(
            success=False,
            message='Timeout handling not implemented - requires manual intervention'
        )

    def _is_stale_lock(self, lock_file: Path) -> bool:
        """Check if a lock file is stale."""
        try:
            # Check file age (stale if older than 1 hour)
            age_seconds = (datetime.now().timestamp() - lock_file.stat().st_mtime)
            return age_seconds > 3600
        except Exception:
            return False


class KnowledgeBase:
    """Stores historical healing attempts and outcomes (MAPE-K Knowledge)."""

    def __init__(self, db_path: Path):
        """
        Initialize knowledge base.

        Args:
            db_path: Path to knowledge base database file
        """
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initialize knowledge base database schema."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(str(self.db_path), timeout=30.0) as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS healing_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    error_type TEXT NOT NULL,
                    error_line TEXT,
                    diagnosis TEXT,
                    action_taken TEXT,
                    success BOOLEAN,
                    timestamp TEXT,
                    context TEXT
                )
            ''')
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_error_type
                ON healing_history(error_type)
            ''')
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_timestamp
                ON healing_history(timestamp)
            ''')

    def record_healing(self, diagnosis: Diagnosis, result: HealingResult):
        """
        Record a healing attempt for learning.

        Args:
            diagnosis: Diagnosis that led to healing action
            result: Result of the healing action
        """
        try:
            with sqlite3.connect(str(self.db_path), timeout=30.0) as conn:
                conn.execute('''
                    INSERT INTO healing_history
                    (error_type, error_line, diagnosis, action_taken, success, timestamp, context)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    diagnosis.error.pattern_type,
                    diagnosis.error.line[:500],  # Limit line length
                    diagnosis.root_cause,
                    result.action_taken or diagnosis.recommended_action,
                    result.success,
                    datetime.now().isoformat(),
                    json.dumps(result.context)
                ))
                conn.commit()
                logger.debug(f"Recorded healing attempt for {diagnosis.error.pattern_type}")
        except Exception as e:
            logger.error(f"Failed to record healing in knowledge base: {e}")

    def find_similar(self, error: DetectedError, limit: int = 5) -> List[Dict]:
        """
        Find similar past errors for context.

        Args:
            error: Detected error to find similar cases for
            limit: Maximum number of similar cases to return

        Returns:
            List of similar healing history records
        """
        try:
            with sqlite3.connect(str(self.db_path), timeout=30.0) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute('''
                    SELECT * FROM healing_history
                    WHERE error_type = ? AND success = 1
                    ORDER BY timestamp DESC LIMIT ?
                ''', (error.pattern_type, limit))
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Failed to find similar errors: {e}")
            return []

    def get_statistics(self) -> Dict[str, Dict]:
        """
        Get healing statistics by error type.

        Returns:
            Dictionary of error_type -> statistics
        """
        try:
            with sqlite3.connect(str(self.db_path), timeout=30.0) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute('''
                    SELECT error_type,
                           COUNT(*) as total_attempts,
                           SUM(CASE WHEN success THEN 1 ELSE 0 END) as successful,
                           MAX(timestamp) as last_seen
                    FROM healing_history
                    GROUP BY error_type
                ''')
                stats = {}
                for row in cursor.fetchall():
                    stats[row['error_type']] = {
                        'total_attempts': row['total_attempts'],
                        'successful': row['successful'],
                        'success_rate': row['successful'] / row['total_attempts'] if row['total_attempts'] > 0 else 0,
                        'last_seen': row['last_seen']
                    }
                return stats
        except Exception as e:
            logger.error(f"Failed to get statistics: {e}")
            return {}

    def cleanup_old_records(self, days: int = 30, max_records_per_type: int = 1000):
        """
        Clean up old healing records to prevent unbounded growth.

        Args:
            days: Remove records older than this many days
            max_records_per_type: Keep at most this many records per error type
        """
        try:
            cutoff_date = datetime.now().timestamp() - (days * 24 * 3600)
            cutoff_iso = datetime.fromtimestamp(cutoff_date).isoformat()

            with sqlite3.connect(str(self.db_path), timeout=30.0) as conn:
                # Remove old records
                cursor = conn.execute('''
                    DELETE FROM healing_history
                    WHERE timestamp < ?
                ''', (cutoff_iso,))
                deleted = cursor.rowcount

                # Limit records per type
                conn.execute('''
                    DELETE FROM healing_history
                    WHERE id NOT IN (
                        SELECT id FROM healing_history h1
                        WHERE (
                            SELECT COUNT(*) FROM healing_history h2
                            WHERE h2.error_type = h1.error_type
                            AND h2.timestamp >= h1.timestamp
                        ) <= ?
                    )
                ''', (max_records_per_type,))

                conn.commit()
                logger.info(f"Cleaned up {deleted} old healing records")
        except Exception as e:
            logger.error(f"Failed to cleanup old records: {e}")
