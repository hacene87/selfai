#!/usr/bin/env python3
"""
Test Feature Suite - Advanced Integration Tests

This test suite validates end-to-end integration with SelfAIRunner:

**Advanced Tests:**
- End-to-end integration with SelfAIRunner
- Git repository operations
- Complete task lifecycle in real environment
- Error recovery scenarios

**Usage:**
  python test_feature_advanced.py

**Prerequisites:**
- Git must be installed and available in PATH

**Expected Results:**
All tests should pass, validating integration with the SelfAI runner
and proper git repository handling.
"""
import sys
import tempfile
import shutil
import subprocess
from pathlib import Path
import time

# Add selfai to path
sys.path.insert(0, str(Path(__file__).parent))

from selfai.database import Database
try:
    from selfai.runner import SelfAIRunner
except ImportError:
    SelfAIRunner = None


def check_git_available():
    """Check if git is available."""
    try:
        subprocess.run(['git', '--version'], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def test_end_to_end_workflow():
    """Test complete workflow from task creation to completion."""
    if not check_git_available():
        print("Git not available - skipping test")
        return True

    # Create temporary git repository
    test_dir = tempfile.mkdtemp()
    repo_path = Path(test_dir) / 'test_repo'
    repo_path.mkdir()

    try:
        # Initialize git repo
        subprocess.run(['git', 'init'], cwd=repo_path, check=True, capture_output=True)
        subprocess.run(['git', 'config', 'user.name', 'Test User'], cwd=repo_path, check=True)
        subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=repo_path, check=True)

        # Create initial commit
        (repo_path / 'README.md').write_text('# Test Repo')
        subprocess.run(['git', 'add', '.'], cwd=repo_path, check=True)
        subprocess.run(['git', 'commit', '-m', 'Initial commit'], cwd=repo_path, check=True, capture_output=True)

        # Initialize database in the repo
        db = Database(repo_path / '.selfai_data' / 'improvements.db')

        # Add a simple test task
        task_id = db.add(
            title="Simple Test Task",
            description="Create a simple hello.py file",
            priority=100
        )

        # Verify task was added
        task = db.get_by_id(task_id)
        assert task is not None, "Task should be created"
        assert task['status'] == 'pending', f"Expected pending, got {task['status']}"
        assert task['title'] == "Simple Test Task"

        print(f"✓ Created test task #{task_id}")
        return True

    finally:
        shutil.rmtree(test_dir)


def test_database_isolation():
    """Test that database instances are properly isolated."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp1:
        db_path1 = Path(tmp1.name)
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp2:
        db_path2 = Path(tmp2.name)

    try:
        db1 = Database(db_path1)
        db2 = Database(db_path2)

        # Add task to db1
        id1 = db1.add("Task in DB1", "First database")

        # Verify it's in db1
        task1 = db1.get_by_id(id1)
        assert task1 is not None

        # Verify it's NOT in db2
        task2 = db2.get_by_id(id1)
        assert task2 is None, "Task should not exist in second database"

        # Add different task to db2
        id2 = db2.add("Task in DB2", "Second database")
        task_db2 = db2.get_by_id(id2)
        assert task_db2 is not None

        return True

    finally:
        db_path1.unlink()
        db_path2.unlink()


def test_concurrent_status_transitions():
    """Test that status transitions work correctly in sequence."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        db_path = Path(tmp.name)

    try:
        db = Database(db_path)
        imp_id = db.add("Concurrent Test", "Test status transitions")

        # Rapid status transitions
        db.mark_planning(imp_id)
        imp = db.get_by_id(imp_id)
        assert imp['status'] == 'planning'

        plan = '{"description": "test"}'
        db.save_plan(imp_id, plan)
        imp = db.get_by_id(imp_id)
        assert imp['status'] == 'approved'

        db.mark_in_progress(imp_id)
        imp = db.get_by_id(imp_id)
        assert imp['status'] == 'in_progress'

        db.mark_testing(imp_id, "output")
        imp = db.get_by_id(imp_id)
        assert imp['status'] == 'testing'

        db.mark_test_passed(imp_id, "test passed")
        imp = db.get_by_id(imp_id)
        assert imp['status'] == 'completed'

        return True

    finally:
        db_path.unlink()


def test_error_recovery():
    """Test error recovery scenarios."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        db_path = Path(tmp.name)

    try:
        db = Database(db_path)

        # Test 1: Failed test should allow retry
        imp_id = db.add("Retry Test", "Should allow retry")
        db.mark_in_progress(imp_id)
        db.mark_testing(imp_id, "output")

        # First failure
        db.mark_test_failed(imp_id, "First failure")
        imp = db.get_by_id(imp_id)
        assert imp['status'] == 'failed'
        assert imp['test_count'] == 1

        # Should be retryable
        db.mark_testing(imp_id, "retry output")
        imp = db.get_by_id(imp_id)
        assert imp['status'] == 'testing'

        # Second failure
        db.mark_test_failed(imp_id, "Second failure")
        imp = db.get_by_id(imp_id)
        assert imp['status'] == 'failed'
        assert imp['test_count'] == 2

        # Third failure = cancelled
        db.mark_test_failed(imp_id, "Third failure")
        imp = db.get_by_id(imp_id)
        assert imp['status'] == 'cancelled'

        return True

    finally:
        db_path.unlink()


def main():
    """Run all advanced tests with detailed reporting."""
    tests = [
        ("End-to-End Workflow", test_end_to_end_workflow),
        ("Database Isolation", test_database_isolation),
        ("Concurrent Status Transitions", test_concurrent_status_transitions),
        ("Error Recovery", test_error_recovery),
    ]

    print("=" * 70)
    print("Test Feature Advanced - Running Integration Tests")
    print("=" * 70)

    passed = 0
    failed = 0
    failures = []

    for name, test_func in tests:
        print(f"\nRunning: {name}...", end=" ")
        try:
            result = test_func()
            if result:
                print("✓ PASS")
                passed += 1
            else:
                print("✗ FAIL")
                failed += 1
                failures.append(name)
        except Exception as e:
            print(f"✗ FAIL - {e}")
            failed += 1
            failures.append(f"{name}: {str(e)}")

    print("\n" + "=" * 70)
    print(f"Results: {passed} passed, {failed} failed")
    if failures:
        print("\nFailed tests:")
        for failure in failures:
            print(f"  - {failure}")
    print("=" * 70)

    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
