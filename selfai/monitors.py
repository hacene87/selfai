"""
Real-time log monitoring and error pattern detection for self-healing system.
"""

import re
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass

from watchdog.observers import Observer
from watchdog.events import PatternMatchingEventHandler, FileSystemEvent

logger = logging.getLogger(__name__)

# Error patterns to detect
ERROR_PATTERNS = {
    'database_locked': r'(database is locked|OperationalError.*locked)',
    'too_many_files': r'(too many open files|OSError.*24)',
    'worktree_conflict': r'(worktree.*conflict|worktree.*locked)',
    'lock_file_stuck': r'(lock.*timeout|unable to create.*lock)',
    'worker_failure': r'(worker.*failed|executor.*error|ThreadPoolExecutor)',
    'timeout': r'(TimeoutExpired|timed out|timeout)',
    'git_error': r'(git.*error|fatal:.*git)',
}


@dataclass
class ErrorPattern:
    """Represents an error pattern to detect in logs."""
    pattern_type: str
    regex: re.Pattern
    severity: str  # 'low', 'medium', 'high', 'critical'
    occurrences: List[datetime]
    last_seen: Optional[datetime] = None

    @classmethod
    def from_dict(cls, pattern_type: str, regex_str: str, severity: str = 'medium'):
        """Create ErrorPattern from dictionary configuration."""
        return cls(
            pattern_type=pattern_type,
            regex=re.compile(regex_str, re.IGNORECASE),
            severity=severity,
            occurrences=[]
        )


@dataclass
class DetectedError:
    """Represents a detected error in the logs."""
    pattern_type: str
    line: str
    timestamp: datetime
    severity: str
    file_path: Optional[str] = None


class LogMonitor(PatternMatchingEventHandler):
    """Real-time log file monitor using watchdog."""

    def __init__(self, log_dir: Path, error_callback):
        """
        Initialize log monitor.

        Args:
            log_dir: Directory containing log files to monitor
            error_callback: Callback function to call when error is detected
        """
        super().__init__(patterns=['*.log'], ignore_directories=True)
        self.log_dir = log_dir
        self.error_callback = error_callback
        self.observer = Observer()
        self.file_positions = {}  # Track file read positions

    def on_modified(self, event: FileSystemEvent):
        """Called when log file is modified."""
        if event.src_path.endswith('.log'):
            self._process_new_lines(Path(event.src_path))

    def on_created(self, event: FileSystemEvent):
        """Called when new log file is created."""
        if event.src_path.endswith('.log'):
            logger.info(f"New log file detected: {event.src_path}")
            self.file_positions[event.src_path] = 0

    def _process_new_lines(self, log_file: Path):
        """Read and analyze new log lines."""
        try:
            new_lines = self._get_new_lines(log_file)
            for line in new_lines:
                self.error_callback(line, str(log_file))
        except Exception as e:
            logger.error(f"Error processing log file {log_file}: {e}")

    def _get_new_lines(self, log_file: Path) -> List[str]:
        """Get new lines from log file since last read."""
        file_path = str(log_file)

        # Initialize position if not tracked
        if file_path not in self.file_positions:
            self.file_positions[file_path] = 0

        try:
            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                # Seek to last position
                f.seek(self.file_positions[file_path])

                # Read new lines
                new_lines = f.readlines()

                # Update position
                self.file_positions[file_path] = f.tell()

                return [line.strip() for line in new_lines if line.strip()]
        except FileNotFoundError:
            logger.warning(f"Log file not found: {log_file}")
            return []
        except Exception as e:
            logger.error(f"Error reading log file {log_file}: {e}")
            return []

    def start(self):
        """Start monitoring log directory."""
        if not self.log_dir.exists():
            logger.warning(f"Log directory does not exist: {self.log_dir}")
            self.log_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"Starting log monitor on {self.log_dir}")
        self.observer.schedule(self, str(self.log_dir), recursive=False)
        self.observer.start()

    def stop(self):
        """Stop monitoring."""
        logger.info("Stopping log monitor")
        self.observer.stop()
        self.observer.join()


class ErrorDetector:
    """Detects error patterns in log lines."""

    def __init__(self, patterns: Optional[Dict[str, str]] = None):
        """
        Initialize error detector.

        Args:
            patterns: Dictionary of pattern_name -> regex_string
        """
        self.patterns = self._load_patterns(patterns or ERROR_PATTERNS)
        self.detection_count = {}

    def _load_patterns(self, pattern_dict: Dict[str, str]) -> Dict[str, ErrorPattern]:
        """Load error patterns from dictionary."""
        patterns = {}
        severity_map = {
            'database_locked': 'high',
            'too_many_files': 'critical',
            'worktree_conflict': 'medium',
            'lock_file_stuck': 'high',
            'worker_failure': 'high',
            'timeout': 'medium',
            'git_error': 'medium',
        }

        for name, regex_str in pattern_dict.items():
            severity = severity_map.get(name, 'medium')
            patterns[name] = ErrorPattern.from_dict(name, regex_str, severity)

        return patterns

    def analyze_line(self, line: str, file_path: Optional[str] = None) -> Optional[DetectedError]:
        """
        Analyze a log line for error patterns.

        Args:
            line: Log line to analyze
            file_path: Path to the log file (optional)

        Returns:
            DetectedError if pattern found, None otherwise
        """
        for pattern_name, pattern in self.patterns.items():
            if pattern.regex.search(line):
                # Record occurrence
                now = datetime.now()
                pattern.occurrences.append(now)
                pattern.last_seen = now

                # Update detection count
                self.detection_count[pattern_name] = self.detection_count.get(pattern_name, 0) + 1

                # Create detected error
                error = DetectedError(
                    pattern_type=pattern_name,
                    line=line,
                    timestamp=now,
                    severity=pattern.severity,
                    file_path=file_path
                )

                logger.debug(f"Detected error pattern '{pattern_name}' in: {line[:100]}")
                return error

        return None

    def get_statistics(self) -> Dict[str, int]:
        """Get detection statistics."""
        return self.detection_count.copy()

    def reset_statistics(self):
        """Reset detection statistics."""
        self.detection_count.clear()
        for pattern in self.patterns.values():
            pattern.occurrences.clear()
            pattern.last_seen = None
