"""
Isolated test environment management for parallel test execution.

This module provides TestEnvironment and TestEnvironmentManager classes that create
completely isolated testing environments with:
- Unique port ranges for each test
- Separate SQLite database instances
- Isolated git worktrees for file system separation
- Independent log files for debugging
"""

import os
import socket
import subprocess
import threading
import logging
import uuid
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class WorktreeError(Exception):
    """Exception raised when worktree operations fail."""
    pass


class TestEnvironment:
    """Manages isolated test environment with unique ports, database, and worktree."""

    def __init__(self, task_id: int, base_path: Path):
        """
        Initialize a new test environment.

        Args:
            task_id: Unique identifier for the task
            base_path: Base directory for all test environment resources
        """
        self.task_id = task_id
        self.base_path = base_path
        self.env_id = f"test-env-{task_id}-{uuid.uuid4().hex[:8]}"

        # Port allocation (base + offset)
        self.port_range_start = self._allocate_port_range()

        # Isolated paths
        self.worktree_path: Optional[Path] = None
        self.db_path: Optional[Path] = None
        self.log_path: Optional[Path] = None
        self.database = None
        self.logger = None
        self.log_files: Dict[str, Path] = {}

    def _allocate_port_range(self, range_size: int = 10) -> int:
        """
        Allocate a unique port range for this environment.

        Args:
            range_size: Number of ports to allocate

        Returns:
            Starting port number of the allocated range
        """
        # Start from base port 10000, offset by task_id * range_size
        base_port = 10000 + (self.task_id * range_size)
        return self._find_available_port_range(base_port, range_size)

    def _find_available_port_range(self, start: int, size: int) -> int:
        """
        Find an available port range by testing socket binding.

        Args:
            start: Starting port to check
            size: Number of consecutive ports needed

        Returns:
            Starting port of available range

        Raises:
            RuntimeError: If no available port range found
        """
        for offset in range(0, 50000, size):
            port = start + offset
            if port + size > 65535:
                break
            if self._is_port_range_available(port, size):
                return port
        raise RuntimeError("No available port range found")

    def _is_port_range_available(self, start: int, size: int) -> bool:
        """
        Check if entire port range is available.

        Args:
            start: Starting port
            size: Number of ports to check

        Returns:
            True if all ports in range are available
        """
        for port in range(start, start + size):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    s.bind(('127.0.0.1', port))
            except OSError:
                return False
        return True

    def get_port(self, service: str) -> int:
        """
        Get allocated port for a specific service.

        Args:
            service: Service name (main, database, cache, api, worker)

        Returns:
            Port number for the service
        """
        service_offsets = {
            'main': 0,
            'database': 1,
            'cache': 2,
            'api': 3,
            'worker': 4,
        }
        offset = service_offsets.get(service, hash(service) % 10)
        return self.port_range_start + offset

    def setup_database(self) -> Path:
        """
        Create isolated database for this test environment.

        Returns:
            Path to the created database file
        """
        db_dir = self.base_path / 'test_databases' / self.env_id
        db_dir.mkdir(parents=True, exist_ok=True)

        self.db_path = db_dir / 'test.db'

        # Create fresh database with schema
        from selfai.database import Database
        self.database = Database(self.db_path)

        logger.info(f"Created isolated database: {self.db_path}")
        return self.db_path

    def get_database(self):
        """
        Get the isolated database instance.

        Returns:
            Database instance for this environment
        """
        if not self.database:
            self.setup_database()
        return self.database

    def setup_worktree(self, repo_path: Path, branch_name: Optional[str] = None) -> Path:
        """
        Create isolated git worktree for this test environment.

        Args:
            repo_path: Path to the main git repository
            branch_name: Optional custom branch name

        Returns:
            Path to the created worktree

        Raises:
            WorktreeError: If worktree creation fails
        """
        worktree_dir = self.base_path / 'test_worktrees' / self.env_id
        worktree_dir.parent.mkdir(parents=True, exist_ok=True)

        branch = branch_name or f"test/{self.env_id}"

        # Create worktree with new branch
        result = subprocess.run(
            ['git', 'worktree', 'add', '-b', branch, str(worktree_dir)],
            cwd=str(repo_path),
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            raise WorktreeError(f"Failed to create worktree: {result.stderr}")

        self.worktree_path = worktree_dir
        logger.info(f"Created isolated worktree: {worktree_dir} on branch {branch}")
        return worktree_dir

    def setup_logging(self) -> Path:
        """
        Create isolated log files for this test environment.

        Returns:
            Path to the log directory
        """
        log_dir = self.base_path / 'test_logs' / self.env_id
        log_dir.mkdir(parents=True, exist_ok=True)

        self.log_path = log_dir

        # Create separate log files
        self.log_files = {
            'main': log_dir / 'main.log',
            'test': log_dir / 'test.log',
            'error': log_dir / 'error.log',
        }

        # Configure isolated logger
        self.logger = logging.getLogger(f'selfai.test.{self.env_id}')
        handler = logging.FileHandler(self.log_files['main'])
        handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(levelname)s - %(name)s - %(message)s'
        ))
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.DEBUG)

        return log_dir

    def get_log_file(self, log_type: str = 'main') -> Path:
        """
        Get path to specific log file.

        Args:
            log_type: Type of log file (main, test, error)

        Returns:
            Path to the requested log file
        """
        return self.log_files.get(log_type, self.log_files['main'])

    def get_environment_variables(self) -> Dict[str, str]:
        """
        Get environment variables for isolated test execution.

        Returns:
            Dictionary of environment variables for this test environment
        """
        return {
            'SELFAI_TEST_ENV_ID': self.env_id,
            'SELFAI_TEST_PORT_BASE': str(self.port_range_start),
            'SELFAI_TEST_DB_PATH': str(self.db_path) if self.db_path else '',
            'SELFAI_TEST_LOG_DIR': str(self.log_path) if self.log_path else '',
            'SELFAI_TEST_WORKTREE': str(self.worktree_path) if self.worktree_path else '',
            # Prevent interference with main instance
            'SELFAI_ISOLATED_TEST': '1',
        }

    def as_subprocess_env(self) -> Dict[str, str]:
        """
        Get full environment dict for subprocess execution.

        Returns:
            Complete environment dictionary including system vars
        """
        env = os.environ.copy()
        env.update(self.get_environment_variables())
        return env

    def setup(self, repo_path: Path) -> 'TestEnvironment':
        """
        Full setup of isolated test environment.

        Args:
            repo_path: Path to the git repository

        Returns:
            Self for method chaining
        """
        self.setup_database()
        self.setup_worktree(repo_path)
        self.setup_logging()

        self.logger.info(f"Test environment {self.env_id} fully initialized")
        self.logger.info(f"  - Database: {self.db_path}")
        self.logger.info(f"  - Worktree: {self.worktree_path}")
        self.logger.info(f"  - Port range: {self.port_range_start}-{self.port_range_start + 9}")

        return self

    def cleanup(self):
        """Clean up all isolated resources."""
        errors = []

        # Cleanup worktree
        if self.worktree_path and self.worktree_path.exists():
            try:
                subprocess.run(
                    ['git', 'worktree', 'remove', '--force', str(self.worktree_path)],
                    capture_output=True
                )
                # Delete branch
                subprocess.run(
                    ['git', 'branch', '-D', f"test/{self.env_id}"],
                    capture_output=True
                )
            except Exception as e:
                errors.append(f"Worktree cleanup failed: {e}")

        # Cleanup database
        if self.db_path and self.db_path.exists():
            try:
                if self.database:
                    # Close database connections
                    self.database = None
                self.db_path.unlink()
                # Try to remove parent directory if empty
                try:
                    self.db_path.parent.rmdir()
                except OSError:
                    pass  # Directory not empty or doesn't exist
            except Exception as e:
                errors.append(f"Database cleanup failed: {e}")

        # Keep logs for debugging (optional cleanup after retention period)

        if errors:
            logger.warning(f"Cleanup errors for {self.env_id}: {errors}")

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit with cleanup."""
        self.cleanup()
        return False


class TestEnvironmentManager:
    """Manages multiple isolated test environments for parallel execution."""

    def __init__(self, repo_path: Path, max_environments: int = 5):
        """
        Initialize the test environment manager.

        Args:
            repo_path: Path to the git repository
            max_environments: Maximum number of concurrent environments
        """
        self.repo_path = repo_path
        self.max_environments = max_environments
        self.base_path = repo_path / '.selfai_data'
        self.active_environments: Dict[int, TestEnvironment] = {}
        self._lock = threading.Lock()

    def create_environment(self, task_id: int) -> TestEnvironment:
        """
        Create a new isolated test environment.

        Args:
            task_id: Unique task identifier

        Returns:
            Newly created TestEnvironment

        Raises:
            RuntimeError: If maximum concurrent environments exceeded
        """
        with self._lock:
            if len(self.active_environments) >= self.max_environments:
                raise RuntimeError(f"Maximum {self.max_environments} concurrent test environments")

            if task_id in self.active_environments:
                return self.active_environments[task_id]

            env = TestEnvironment(task_id, self.base_path)
            env.setup(self.repo_path)
            self.active_environments[task_id] = env

            return env

    def get_environment(self, task_id: int) -> Optional[TestEnvironment]:
        """
        Get existing environment for task.

        Args:
            task_id: Task identifier

        Returns:
            TestEnvironment if exists, None otherwise
        """
        return self.active_environments.get(task_id)

    def release_environment(self, task_id: int):
        """
        Release and cleanup environment.

        Args:
            task_id: Task identifier to release
        """
        with self._lock:
            if task_id in self.active_environments:
                self.active_environments[task_id].cleanup()
                del self.active_environments[task_id]

    def cleanup_all(self):
        """Cleanup all active environments."""
        for task_id in list(self.active_environments.keys()):
            self.release_environment(task_id)
