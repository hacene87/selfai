"""
Main monitoring orchestrator for SelfAI self-healing system.
Implements MAPE-K loop (Monitor, Analyze, Plan, Execute, Knowledge) pattern.
"""

import json
import logging
import threading
import time
from pathlib import Path
from typing import Dict, Optional

from .monitors import LogMonitor, ErrorDetector, DetectedError
from .healers import ErrorAnalyzer, SelfHealingExecutor, KnowledgeBase

logger = logging.getLogger(__name__)


class SelfHealingMonitor:
    """Main orchestrator for monitoring and self-healing."""

    def __init__(
        self,
        repo_path: Path,
        config: Optional[Dict] = None,
        auto_heal: bool = True,
        min_confidence: float = 0.6
    ):
        """
        Initialize self-healing monitor.

        Args:
            repo_path: Path to repository root
            config: Optional configuration dictionary
            auto_heal: Whether to automatically execute healing actions
            min_confidence: Minimum confidence threshold for auto-healing
        """
        self.repo_path = repo_path
        self.data_dir = repo_path / '.selfai_data'
        self.log_dir = self.data_dir / 'logs'
        self.config = config or self._load_default_config()
        self.auto_heal = auto_heal
        self.min_confidence = min_confidence

        # Ensure directories exist
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Initialize components (MAPE-K)
        db_path = self.data_dir / 'data' / 'improvements.db'
        self.knowledge_base = KnowledgeBase(self.data_dir / 'healing.db')
        self.error_analyzer = ErrorAnalyzer(self.knowledge_base)
        self.healer = SelfHealingExecutor(repo_path, db_path)
        self.error_detector = ErrorDetector()

        # Create log monitor with callback
        self.log_monitor = LogMonitor(self.log_dir, self._on_log_line)

        # Metrics
        self.errors_detected = 0
        self.healings_attempted = 0

        # Deduplication: track recently processed error types
        self._recent_errors = {}  # error_type -> last_processed_time
        self._error_cooldown = 60  # seconds to skip duplicate errors
        self.healings_successful = 0

        # Thread control
        self._running = False
        self._health_check_thread = None

        logger.debug(f"Initialized SelfHealingMonitor (auto_heal={auto_heal}, min_confidence={min_confidence})")

    def _load_default_config(self) -> Dict:
        """Load default configuration."""
        return {
            'monitoring': {
                'enabled': True,
                'log_monitoring': True,
                'health_check_interval': 60,
                'auto_heal': True,
                'min_confidence_threshold': 0.6
            },
            'error_patterns': {
                'database_locked': {
                    'severity': 'high',
                    'auto_heal': True,
                    'retry_count': 3,
                    'cooldown_seconds': 10
                },
                'too_many_files': {
                    'severity': 'critical',
                    'auto_heal': True,
                    'alert': True
                },
                'worktree_conflict': {
                    'severity': 'medium',
                    'auto_heal': True
                }
            }
        }

    def _on_log_line(self, line: str, file_path: str):
        """
        Callback for when a new log line is detected.

        Args:
            line: Log line content
            file_path: Path to log file
        """
        # Detect error in line
        error = self.error_detector.analyze_line(line, file_path)
        if error:
            self.process_error(error)

    def start(self):
        """Start the monitoring system."""
        if self._running:
            logger.warning("Monitor is already running")
            return

        logger.info('Starting self-healing monitor...')
        self._running = True

        # Start log monitoring
        try:
            self.log_monitor.start()
        except Exception as e:
            logger.error(f"Failed to start log monitor: {e}")

        # Start periodic health checks
        self._start_health_checks()

    def stop(self):
        """Stop the monitoring system."""
        if not self._running:
            return

        logger.info("Stopping self-healing monitor...")
        self._running = False

        # Stop log monitoring
        try:
            self.log_monitor.stop()
        except Exception as e:
            logger.error(f"Error stopping log monitor: {e}")

        # Stop health check thread
        if self._health_check_thread:
            self._health_check_thread.join(timeout=5)

    def process_error(self, error: DetectedError):
        """
        Process a detected error through MAPE-K loop.

        Args:
            error: Detected error to process
        """
        # Deduplication: skip if we recently processed this error type
        current_time = time.time()
        error_key = error.pattern_type
        last_processed = self._recent_errors.get(error_key, 0)

        if current_time - last_processed < self._error_cooldown:
            # Skip duplicate error within cooldown period
            return

        self._recent_errors[error_key] = current_time
        self.errors_detected += 1
        logger.debug(f"Processing detected error: {error.pattern_type}")

        try:
            # Analyze (MAPE-K Analyze phase)
            diagnosis = self.error_analyzer.diagnose(error)

            # Plan (implicit in diagnosis)
            if diagnosis.confidence < self.min_confidence:
                logger.debug(
                    f'Low confidence diagnosis ({diagnosis.confidence:.2f}), '
                    f'skipping auto-heal: {error.pattern_type}'
                )
                return

            if not self.auto_heal:
                logger.info(f"Auto-heal disabled, skipping: {error.pattern_type}")
                return

            # Execute (MAPE-K Execute phase)
            self.healings_attempted += 1
            result = self.healer.execute(diagnosis)

            # Knowledge (MAPE-K Knowledge phase) - record result
            self.knowledge_base.record_healing(diagnosis, result)

            if result.success:
                self.healings_successful += 1
                logger.info(f'Successfully healed: {error.pattern_type} - {result.message}')
            else:
                logger.error(f'Healing failed: {error.pattern_type} - {result.message}')

        except Exception as e:
            logger.error(f"Error processing detected error: {e}", exc_info=True)

    def _start_health_checks(self):
        """Start periodic health checks in background thread."""
        interval = self.config.get('monitoring', {}).get('health_check_interval', 60)

        def health_check_loop():
            while self._running:
                try:
                    self._perform_health_check()
                except Exception as e:
                    logger.error(f"Health check failed: {e}")

                # Sleep with interrupt check
                for _ in range(interval):
                    if not self._running:
                        break
                    time.sleep(1)

        self._health_check_thread = threading.Thread(target=health_check_loop, daemon=True)
        self._health_check_thread.start()
        logger.info(f"Started health check thread (interval={interval}s)")

    def _perform_health_check(self):
        """Perform periodic health check."""
        logger.debug("Performing health check...")

        # Check if log directory exists and is writable
        if not self.log_dir.exists():
            logger.warning(f"Log directory does not exist: {self.log_dir}")
            self.log_dir.mkdir(parents=True, exist_ok=True)

        # Cleanup old knowledge base records periodically
        try:
            self.knowledge_base.cleanup_old_records(days=30, max_records_per_type=1000)
        except Exception as e:
            logger.error(f"Failed to cleanup knowledge base: {e}")

    def get_metrics(self) -> Dict:
        """
        Get monitoring metrics.

        Returns:
            Dictionary of metrics
        """
        success_rate = 0.0
        if self.healings_attempted > 0:
            success_rate = self.healings_successful / self.healings_attempted

        return {
            'errors_detected': self.errors_detected,
            'healings_attempted': self.healings_attempted,
            'healings_successful': self.healings_successful,
            'success_rate': success_rate,
            'running': self._running
        }

    def get_detailed_stats(self) -> Dict:
        """
        Get detailed statistics including knowledge base data.

        Returns:
            Dictionary of detailed statistics
        """
        metrics = self.get_metrics()

        # Add knowledge base statistics
        kb_stats = self.knowledge_base.get_statistics()
        metrics['knowledge_base'] = kb_stats

        # Add error detector statistics
        detection_stats = self.error_detector.get_statistics()
        metrics['detections_by_type'] = detection_stats

        return metrics

    def reset_metrics(self):
        """Reset monitoring metrics."""
        self.errors_detected = 0
        self.healings_attempted = 0
        self.healings_successful = 0
        self.error_detector.reset_statistics()
        logger.info("Reset monitoring metrics")


def create_monitor(
    repo_path: Path,
    config_path: Optional[Path] = None,
    auto_heal: bool = True,
    min_confidence: float = 0.6
) -> SelfHealingMonitor:
    """
    Factory function to create and configure a SelfHealingMonitor.

    Args:
        repo_path: Path to repository root
        config_path: Optional path to configuration file
        auto_heal: Whether to automatically execute healing actions
        min_confidence: Minimum confidence threshold for auto-healing

    Returns:
        Configured SelfHealingMonitor instance
    """
    config = None
    if config_path and config_path.exists():
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
            logger.info(f"Loaded configuration from {config_path}")
        except Exception as e:
            logger.error(f"Failed to load configuration: {e}")

    return SelfHealingMonitor(
        repo_path=repo_path,
        config=config,
        auto_heal=auto_heal,
        min_confidence=min_confidence
    )
