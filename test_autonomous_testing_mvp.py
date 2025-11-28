"""Manual tests for Autonomous Testing and Validation MVP functionality."""
import tempfile
import shutil
import subprocess
import sqlite3
from pathlib import Path


def init_git_repo(path: Path):
    """Initialize a git repository."""
    subprocess.run(['git', 'init'], cwd=str(path), capture_output=True)
    subprocess.run(['git', 'config', 'user.name', 'Test User'], cwd=str(path), capture_output=True)
    subprocess.run(['git', 'config', 'user.email', 'test@test.com'], cwd=str(path), capture_output=True)

    # Create initial commit
    test_file = path / 'README.md'
    test_file.write_text('# Test Repo\n')
    subprocess.run(['git', 'add', '.'], cwd=str(path), capture_output=True)
    subprocess.run(['git', 'commit', '-m', 'Initial commit'], cwd=str(path), capture_output=True)
    subprocess.run(['git', 'branch', '-M', 'main'], cwd=str(path), capture_output=True)


def test_parse_test_result_pass_json():
    """Test that _parse_test_result correctly identifies pass from JSON."""
    from selfai.runner import Runner

    test_dir = tempfile.mkdtemp()
    try:
        repo_path = Path(test_dir) / 'test_repo'
        repo_path.mkdir()
        init_git_repo(repo_path)

        runner = Runner(repo_path)

        # Test valid pass JSON
        output1 = '```json\n{"test_passed": true, "tests_run": ["import check"]}\n```'
        if runner._parse_test_result(output1):
            print("✓ Parsed 'test_passed: true' correctly")
        else:
            print("✗ Failed to parse 'test_passed: true'")
            return False

        # Test with no spaces
        output2 = '{"test_passed":true}'
        if runner._parse_test_result(output2):
            print("✓ Parsed 'test_passed:true' (no space) correctly")
        else:
            print("✗ Failed to parse 'test_passed:true'")
            return False

        return True
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_parse_test_result_fail_json():
    """Test that _parse_test_result correctly identifies fail from JSON."""
    from selfai.runner import Runner

    test_dir = tempfile.mkdtemp()
    try:
        repo_path = Path(test_dir) / 'test_repo'
        repo_path.mkdir()
        init_git_repo(repo_path)

        runner = Runner(repo_path)

        # Test valid fail JSON
        output1 = '```json\n{"test_passed": false, "remaining_issues": ["syntax error"]}\n```'
        if not runner._parse_test_result(output1):
            print("✓ Parsed 'test_passed: false' correctly")
        else:
            print("✗ Failed to parse 'test_passed: false'")
            return False

        # Test with no spaces
        output2 = '{"test_passed":false}'
        if not runner._parse_test_result(output2):
            print("✓ Parsed 'test_passed:false' (no space) correctly")
        else:
            print("✗ Failed to parse 'test_passed:false'")
            return False

        return True
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_parse_test_result_heuristic():
    """Test fallback heuristic parsing for non-JSON output."""
    from selfai.runner import Runner

    test_dir = tempfile.mkdtemp()
    try:
        repo_path = Path(test_dir) / 'test_repo'
        repo_path.mkdir()
        init_git_repo(repo_path)

        runner = Runner(repo_path)

        # Test positive heuristics
        output_pass = "All tests passed. Feature is working correctly and verified."
        if runner._parse_test_result(output_pass):
            print("✓ Heuristic correctly detected pass indicators")
        else:
            print("✗ Heuristic failed to detect pass indicators")
            return False

        # Test negative heuristics
        output_fail = "Test failed with exception. Error: module not found. Code is broken."
        if not runner._parse_test_result(output_fail):
            print("✓ Heuristic correctly detected fail indicators")
        else:
            print("✗ Heuristic failed to detect fail indicators")
            return False

        return True
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_mark_test_passed():
    """Test that mark_test_passed correctly updates database."""
    from selfai.database import Database

    test_dir = tempfile.mkdtemp()
    try:
        db_path = Path(test_dir) / 'test.db'
        db = Database(db_path)

        # Add a test improvement
        imp_id = db.add(title="Test Feature", description="Test", category="test", priority=50, source="test")

        # Mark test as passed at level 1 (MVP)
        result = db.mark_test_passed(imp_id, level=1, test_output="All tests passed")

        if not result:
            print("✗ mark_test_passed returned False")
            return False

        # Verify database state
        improvement = db.get_by_id(imp_id)
        if improvement['mvp_test_status'] == 'passed':
            print("✓ mvp_test_status set to 'passed'")
        else:
            print(f"✗ mvp_test_status is '{improvement['mvp_test_status']}', expected 'passed'")
            return False

        if improvement['status'] == 'completed':
            print("✓ status set to 'completed'")
        else:
            print(f"✗ status is '{improvement['status']}', expected 'completed'")
            return False

        if improvement['mvp_test_output'] == "All tests passed":
            print("✓ test_output stored correctly")
        else:
            print("✗ test_output not stored correctly")
            return False

        return True
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_mark_test_failed():
    """Test that mark_test_failed correctly updates database and increments retry."""
    from selfai.database import Database

    test_dir = tempfile.mkdtemp()
    try:
        db_path = Path(test_dir) / 'test.db'
        db = Database(db_path)

        # Add a test improvement
        imp_id = db.add(title="Test Feature", description="Test", category="test", priority=50, source="test")

        # Mark test as failed at level 1 (MVP)
        result = db.mark_test_failed(imp_id, level=1, test_output="Syntax error found")

        if not result:
            print("✗ mark_test_failed returned False")
            return False

        # Verify database state
        improvement = db.get_by_id(imp_id)
        if improvement['mvp_test_status'] == 'failed':
            print("✓ mvp_test_status set to 'failed'")
        else:
            print(f"✗ mvp_test_status is '{improvement['mvp_test_status']}', expected 'failed'")
            return False

        if improvement['status'] == 'pending':
            print("✓ status set to 'pending' for retry")
        else:
            print(f"✗ status is '{improvement['status']}', expected 'pending'")
            return False

        if improvement['retry_count'] == 1:
            print("✓ retry_count incremented to 1")
        else:
            print(f"✗ retry_count is {improvement['retry_count']}, expected 1")
            return False

        # Test that multiple failures increment retry_count
        db.mark_test_failed(imp_id, level=1, test_output="Still failing")
        improvement = db.get_by_id(imp_id)
        if improvement['retry_count'] == 2:
            print("✓ retry_count correctly incremented to 2 on second failure")
        else:
            print(f"✗ retry_count is {improvement['retry_count']}, expected 2")
            return False

        return True
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_get_test_criteria():
    """Test that test criteria is returned for each level."""
    from selfai.runner import Runner

    test_dir = tempfile.mkdtemp()
    try:
        repo_path = Path(test_dir) / 'test_repo'
        repo_path.mkdir()
        init_git_repo(repo_path)

        runner = Runner(repo_path)

        # Test level 1 (MVP)
        criteria1 = runner._get_test_criteria(1)
        if 'MVP Test Criteria' in criteria1 and 'syntax errors' in criteria1.lower():
            print("✓ MVP test criteria returned correctly")
        else:
            print("✗ MVP test criteria missing expected content")
            return False

        # Test level 2 (Enhanced)
        criteria2 = runner._get_test_criteria(2)
        if 'Enhanced Test Criteria' in criteria2 and 'edge cases' in criteria2.lower():
            print("✓ Enhanced test criteria returned correctly")
        else:
            print("✗ Enhanced test criteria missing expected content")
            return False

        # Test level 3 (Advanced)
        criteria3 = runner._get_test_criteria(3)
        if 'Advanced Test Criteria' in criteria3 and 'performance' in criteria3.lower():
            print("✓ Advanced test criteria returned correctly")
        else:
            print("✗ Advanced test criteria missing expected content")
            return False

        return True
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_cli_test_command_exists():
    """Test that the test command exists in CLI."""
    from selfai.__main__ import main, test_feature

    # Check that test_feature function exists and is callable
    if callable(test_feature):
        print("✓ test_feature function exists and is callable")
    else:
        print("✗ test_feature function not found or not callable")
        return False

    return True


if __name__ == '__main__':
    print("Testing Autonomous Testing and Validation MVP...\n")

    results = []

    print("1. Testing _parse_test_result with pass JSON...")
    results.append(test_parse_test_result_pass_json())
    print()

    print("2. Testing _parse_test_result with fail JSON...")
    results.append(test_parse_test_result_fail_json())
    print()

    print("3. Testing _parse_test_result heuristic fallback...")
    results.append(test_parse_test_result_heuristic())
    print()

    print("4. Testing mark_test_passed database update...")
    results.append(test_mark_test_passed())
    print()

    print("5. Testing mark_test_failed database update and retry increment...")
    results.append(test_mark_test_failed())
    print()

    print("6. Testing _get_test_criteria for all levels...")
    results.append(test_get_test_criteria())
    print()

    print("7. Testing CLI test command exists...")
    results.append(test_cli_test_command_exists())
    print()

    print(f"\nResults: {sum(results)}/{len(results)} tests passed")

    if all(results):
        print("\n✓ All MVP tests passed!")
        exit(0)
    else:
        print("\n✗ Some tests failed")
        exit(1)
