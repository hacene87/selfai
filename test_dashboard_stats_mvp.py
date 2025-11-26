"""Manual tests for Dashboard Success and Failure Statistics MVP functionality."""
import tempfile
import shutil
import subprocess
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


def test_get_success_fail_stats_empty():
    """Test success/fail stats with empty database."""
    from selfai.database import Database

    test_dir = tempfile.mkdtemp()
    try:
        db_path = Path(test_dir) / 'test.db'
        db = Database(db_path)

        stats = db.get_success_fail_stats()

        # Verify all stats are 0 for empty database
        if stats['total_passed'] == 0:
            print("✓ total_passed is 0 for empty database")
        else:
            print(f"✗ total_passed is {stats['total_passed']}, expected 0")
            return False

        if stats['total_failed'] == 0:
            print("✓ total_failed is 0 for empty database")
        else:
            print(f"✗ total_failed is {stats['total_failed']}, expected 0")
            return False

        if stats['success_rate'] == 0.0:
            print("✓ success_rate is 0.0 for empty database")
        else:
            print(f"✗ success_rate is {stats['success_rate']}, expected 0.0")
            return False

        if stats['total_retries'] == 0:
            print("✓ total_retries is 0 for empty database")
        else:
            print(f"✗ total_retries is {stats['total_retries']}, expected 0")
            return False

        if stats['avg_retries'] == 0.0:
            print("✓ avg_retries is 0.0 for empty database")
        else:
            print(f"✗ avg_retries is {stats['avg_retries']}, expected 0.0")
            return False

        return True
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_get_success_fail_stats_with_data():
    """Test success/fail stats with actual data."""
    from selfai.database import Database

    test_dir = tempfile.mkdtemp()
    try:
        db_path = Path(test_dir) / 'test.db'
        db = Database(db_path)

        # Add test improvements
        imp1 = db.add(title="Feature 1", description="Test", category="test", priority=50, source="test")
        imp2 = db.add(title="Feature 2", description="Test", category="test", priority=50, source="test")
        imp3 = db.add(title="Feature 3", description="Test", category="test", priority=50, source="test")

        # Feature 1: MVP passed
        db.mark_test_passed(imp1, level=1, test_output="Passed")

        # Feature 2: MVP failed, Enhanced passed
        db.mark_test_failed(imp2, level=1, test_output="Failed")
        db.mark_test_passed(imp2, level=2, test_output="Passed")

        # Feature 3: MVP failed twice, then passed
        db.mark_test_failed(imp3, level=1, test_output="Failed")
        db.mark_test_failed(imp3, level=1, test_output="Failed again")
        db.mark_test_passed(imp3, level=1, test_output="Passed")

        stats = db.get_success_fail_stats()

        # Verify counts
        if stats['mvp_passed'] == 2:
            print("✓ mvp_passed count is correct (2)")
        else:
            print(f"✗ mvp_passed is {stats['mvp_passed']}, expected 2")
            return False

        if stats['mvp_failed'] == 1:
            print("✓ mvp_failed count is correct (1)")
        else:
            print(f"✗ mvp_failed is {stats['mvp_failed']}, expected 1")
            return False

        if stats['enhanced_passed'] == 1:
            print("✓ enhanced_passed count is correct (1)")
        else:
            print(f"✗ enhanced_passed is {stats['enhanced_passed']}, expected 1")
            return False

        if stats['total_passed'] == 3:
            print("✓ total_passed is correct (3)")
        else:
            print(f"✗ total_passed is {stats['total_passed']}, expected 3")
            return False

        if stats['total_failed'] == 1:
            print("✓ total_failed is correct (1)")
        else:
            print(f"✗ total_failed is {stats['total_failed']}, expected 1")
            return False

        # Verify success rate (3 passed / 4 total = 75%)
        if stats['success_rate'] == 75.0:
            print("✓ success_rate is correct (75.0%)")
        else:
            print(f"✗ success_rate is {stats['success_rate']}, expected 75.0")
            return False

        # Verify retry counts (feature 3 has 2 retries)
        if stats['total_retries'] == 3:
            print("✓ total_retries is correct (3)")
        else:
            print(f"✗ total_retries is {stats['total_retries']}, expected 3")
            return False

        # Average retries: 3 retries / 3 features = 1.0
        if stats['avg_retries'] == 1.0:
            print("✓ avg_retries is correct (1.0)")
        else:
            print(f"✗ avg_retries is {stats['avg_retries']}, expected 1.0")
            return False

        return True
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_dashboard_includes_stats():
    """Test that dashboard HTML includes success/fail statistics."""
    from selfai.runner import Runner

    test_dir = tempfile.mkdtemp()
    try:
        repo_path = Path(test_dir) / 'test_repo'
        repo_path.mkdir()
        init_git_repo(repo_path)

        runner = Runner(repo_path)

        # Add a test improvement with passed test
        imp_id = runner.db.add(title="Test Feature", description="Test", category="test",
                               priority=50, source="test")
        runner.db.mark_test_passed(imp_id, level=1, test_output="Passed")

        # Update dashboard
        runner.update_dashboard()

        # Read dashboard HTML
        dashboard_path = runner.workspace_path / 'dashboard.html'
        if not dashboard_path.exists():
            print("✗ Dashboard file not created")
            return False

        html_content = dashboard_path.read_text()

        # Check for new stat cards
        if 'Tests Passed' in html_content:
            print("✓ Dashboard contains 'Tests Passed' label")
        else:
            print("✗ Dashboard missing 'Tests Passed' label")
            return False

        if 'Tests Failed' in html_content:
            print("✓ Dashboard contains 'Tests Failed' label")
        else:
            print("✗ Dashboard missing 'Tests Failed' label")
            return False

        if 'Success Rate' in html_content:
            print("✓ Dashboard contains 'Success Rate' label")
        else:
            print("✗ Dashboard missing 'Success Rate' label")
            return False

        if 'Total Retries' in html_content:
            print("✓ Dashboard contains 'Total Retries' label")
        else:
            print("✗ Dashboard missing 'Total Retries' label")
            return False

        if 'Avg Retries/Feature' in html_content:
            print("✓ Dashboard contains 'Avg Retries/Feature' label")
        else:
            print("✗ Dashboard missing 'Avg Retries/Feature' label")
            return False

        # Check for CSS classes
        if 'stat-card success' in html_content:
            print("✓ Dashboard contains success stat card CSS")
        else:
            print("✗ Dashboard missing success stat card CSS")
            return False

        if 'stat-card failure' in html_content:
            print("✓ Dashboard contains failure stat card CSS")
        else:
            print("✗ Dashboard missing failure stat card CSS")
            return False

        return True
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_success_rate_edge_cases():
    """Test success rate calculation with edge cases."""
    from selfai.database import Database

    test_dir = tempfile.mkdtemp()
    try:
        db_path = Path(test_dir) / 'test.db'
        db = Database(db_path)

        # Test with no tests run (pending status)
        imp1 = db.add(title="Pending Feature", description="Test", category="test",
                     priority=50, source="test")

        stats = db.get_success_fail_stats()
        if stats['success_rate'] == 0.0:
            print("✓ Success rate is 0.0 when no tests have run")
        else:
            print(f"✗ Success rate is {stats['success_rate']}, expected 0.0 for no tests")
            return False

        # Test with 100% success
        db.mark_test_passed(imp1, level=1, test_output="Passed")
        stats = db.get_success_fail_stats()
        if stats['success_rate'] == 100.0:
            print("✓ Success rate is 100.0 with all tests passing")
        else:
            print(f"✗ Success rate is {stats['success_rate']}, expected 100.0")
            return False

        # Test with 100% failure
        db2 = Database(Path(test_dir) / 'test2.db')
        imp2 = db2.add(title="Failed Feature", description="Test", category="test",
                      priority=50, source="test")
        db2.mark_test_failed(imp2, level=1, test_output="Failed")

        stats2 = db2.get_success_fail_stats()
        if stats2['success_rate'] == 0.0:
            print("✓ Success rate is 0.0 with all tests failing")
        else:
            print(f"✗ Success rate is {stats2['success_rate']}, expected 0.0 for all failed")
            return False

        return True
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


if __name__ == '__main__':
    print("Testing Dashboard Success and Failure Statistics MVP...\n")

    results = []

    print("1. Testing get_success_fail_stats with empty database...")
    results.append(test_get_success_fail_stats_empty())
    print()

    print("2. Testing get_success_fail_stats with actual data...")
    results.append(test_get_success_fail_stats_with_data())
    print()

    print("3. Testing dashboard includes statistics...")
    results.append(test_dashboard_includes_stats())
    print()

    print("4. Testing success rate edge cases...")
    results.append(test_success_rate_edge_cases())
    print()

    print(f"\nResults: {sum(results)}/{len(results)} tests passed")

    if all(results):
        print("\n✓ All MVP tests passed!")
        exit(0)
    else:
        print("\n✗ Some tests failed")
        exit(1)
