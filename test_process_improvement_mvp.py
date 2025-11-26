"""Quick MVP test for _process_improvement method."""
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch
from selfai.runner import Runner
from selfai.database import Database

def test_process_improvement_basic():
    """Test basic _process_improvement flow."""
    # Create temporary test directory
    test_dir = tempfile.mkdtemp()
    try:
        repo_path = Path(test_dir) / 'test_repo'
        repo_path.mkdir()
        runner = Runner(repo_path)

        # Mock external dependencies
        runner._create_plan = MagicMock(return_value="Test plan")
        runner._execute_plan = MagicMock(return_value="Test output")
        runner.update_dashboard = MagicMock()

        # Create a test improvement in database
        runner.db.add(
            title="Test Feature",
            description="Test description",
            category="feature",
            priority=50,
            source="test"
        )

        # Get the improvement
        improvement = runner.db.get_all()[0]

        # Process it
        runner._process_improvement(improvement)

        # Verify methods were called
        assert runner._create_plan.called, "_create_plan should be called"
        assert runner._execute_plan.called, "_execute_plan should be called"
        assert runner.update_dashboard.called, "update_dashboard should be called"

        # Verify improvement was marked as completed
        updated = runner.db.get_by_id(improvement['id'])
        assert updated['status'] in ['needs_testing', 'completed'], f"Status should be updated, got: {updated['status']}"

        print("✓ Test passed: _process_improvement basic flow works")
        return True
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)

def test_process_improvement_handles_plan_failure():
    """Test that _process_improvement handles planning failures."""
    test_dir = tempfile.mkdtemp()
    try:
        repo_path = Path(test_dir) / 'test_repo'
        repo_path.mkdir()
        runner = Runner(repo_path)

        # Mock to return None (planning failed)
        runner._create_plan = MagicMock(return_value=None)
        runner._execute_plan = MagicMock()
        runner.update_dashboard = MagicMock()

        # Create a test improvement
        runner.db.add(
            title="Test Feature",
            description="Test description",
            category="feature",
            priority=50,
            source="test"
        )

        improvement = runner.db.get_all()[0]

        # Process it
        runner._process_improvement(improvement)

        # Verify _execute_plan was NOT called (because planning failed)
        assert not runner._execute_plan.called, "_execute_plan should not be called when planning fails"

        # Verify improvement was marked as failed
        updated = runner.db.get_by_id(improvement['id'])
        assert updated['status'] == 'failed', f"Status should be 'failed', got: {updated['status']}"

        print("✓ Test passed: _process_improvement handles planning failure")
        return True
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)

def test_process_improvement_handles_execution_failure():
    """Test that _process_improvement handles execution failures."""
    test_dir = tempfile.mkdtemp()
    try:
        repo_path = Path(test_dir) / 'test_repo'
        repo_path.mkdir()
        runner = Runner(repo_path)

        # Mock to return None (execution failed)
        runner._create_plan = MagicMock(return_value="Test plan")
        runner._execute_plan = MagicMock(return_value=None)
        runner.update_dashboard = MagicMock()

        # Create a test improvement
        runner.db.add(
            title="Test Feature",
            description="Test description",
            category="feature",
            priority=50,
            source="test"
        )

        improvement = runner.db.get_all()[0]

        # Process it
        runner._process_improvement(improvement)

        # Verify improvement was marked as failed
        updated = runner.db.get_by_id(improvement['id'])
        assert updated['status'] == 'failed', f"Status should be 'failed', got: {updated['status']}"

        print("✓ Test passed: _process_improvement handles execution failure")
        return True
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)

if __name__ == '__main__':
    print("Testing MVP implementation of _process_improvement...")
    print()

    results = []
    results.append(test_process_improvement_basic())
    results.append(test_process_improvement_handles_plan_failure())
    results.append(test_process_improvement_handles_execution_failure())

    print()
    print(f"Results: {sum(results)}/{len(results)} tests passed")

    if all(results):
        print("\n✓ All MVP tests passed!")
        exit(0)
    else:
        print("\n✗ Some tests failed")
        exit(1)
