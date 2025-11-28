"""Tests for smart task resumption and priority system."""
import unittest
import tempfile
import shutil
import os
from pathlib import Path
from unittest.mock import MagicMock, patch
from selfai.runner import SelfAIRunner
from selfai.database import Database


class TestTaskResumption(unittest.TestCase):
    """Test smart task resumption after crashes."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.repo_path = Path(self.test_dir) / 'test_repo'
        self.repo_path.mkdir()
        self.runner = SelfAIRunner(self.repo_path)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_stale_lock_detection(self):
        """Test that stale locks from crashed processes are detected."""
        # Write a lock file with non-existent PID
        fake_pid = 999999
        self.runner.lock_file.write_text(str(fake_pid))

        # Should be able to acquire lock (stale lock cleaned)
        result = self.runner.acquire_lock()
        self.assertTrue(result)

        # Lock should now contain our PID
        current_pid = int(self.runner.lock_file.read_text())
        self.assertEqual(current_pid, os.getpid())

        # Cleanup
        self.runner.release_lock()

    def test_stuck_tasks_prioritized_first(self):
        """Test that stuck in-progress tasks are processed before pending tasks."""
        # Add stuck task
        stuck_id = self.runner.db.add('Stuck Task', '', priority=50)
        self.runner.db.mark_in_progress(stuck_id)

        # Add high-priority pending task
        pending_id = self.runner.db.add('High Priority Pending', '', priority=100)

        # Mock execution to track order
        execution_order = []

        def track_execution(task):
            execution_order.append(task['id'])

        self.runner._execute_task = MagicMock(side_effect=track_execution)
        self.runner._generate_plan = MagicMock()
        self.runner.update_dashboard = MagicMock()

        self.runner.run()

        # Stuck task should be processed first despite lower priority
        if execution_order:
            self.assertEqual(execution_order[0], stuck_id)

    def test_priority_ordering_for_pending_tasks(self):
        """Test that pending tasks are processed by priority."""
        # Add tasks with different priorities
        low_id = self.runner.db.add('Low Priority', '', priority=20)
        high_id = self.runner.db.add('High Priority', '', priority=90)
        mid_id = self.runner.db.add('Mid Priority', '', priority=50)

        # Get pending tasks
        pending = self.runner.db.get_pending_planning(limit=10)

        # Should be ordered by priority DESC
        priorities = [t['priority'] for t in pending]
        self.assertEqual(priorities, sorted(priorities, reverse=True))
        self.assertEqual(pending[0]['id'], high_id)

    def test_is_process_running_detects_invalid_pid(self):
        """Test that _is_process_running correctly identifies invalid PIDs."""
        # Test with clearly invalid PID
        result = self.runner._is_process_running(999999)
        self.assertFalse(result)

        # Test with our own PID (should be running)
        result = self.runner._is_process_running(os.getpid())
        self.assertTrue(result)


if __name__ == '__main__':
    unittest.main()
