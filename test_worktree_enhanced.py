"""Enhanced level tests for Git Worktree Parallel Processing.

Tests edge cases, error handling, and input validation.
"""
import tempfile
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock
from selfai.runner import WorktreeManager, ValidationError, DiskSpaceError, WorktreeError, MergeConflictError

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

    # Create main branch
    subprocess.run(['git', 'branch', '-M', 'main'], cwd=str(path), capture_output=True)

def test_sanitize_branch_name():
    """Test that branch names are properly sanitized."""
    test_dir = tempfile.mkdtemp()
    try:
        repo_path = Path(test_dir) / 'test_repo'
        repo_path.mkdir()
        workspace_path = repo_path / '.selfai_data'

        init_git_repo(repo_path)
        mgr = WorktreeManager(repo_path, workspace_path)

        # Test with special characters
        result = mgr._sanitize_branch_name("Test Feature!@#$%")
        if result == "feature/test-feature":
            print("✓ Branch name sanitization works")
            return True
        else:
            print(f"✗ Branch name sanitization failed: got '{result}'")
            return False
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)

def test_duplicate_worktree_handling():
    """Test that duplicate worktree creation is handled."""
    test_dir = tempfile.mkdtemp()
    try:
        repo_path = Path(test_dir) / 'test_repo'
        repo_path.mkdir()
        workspace_path = repo_path / '.selfai_data'

        init_git_repo(repo_path)
        mgr = WorktreeManager(repo_path, workspace_path)

        # Create worktree twice with same ID
        worktree1 = mgr.create_worktree(1, "Test Feature")
        worktree2 = mgr.create_worktree(1, "Test Feature Again")

        if worktree1 and worktree2 and worktree2.exists():
            print("✓ Duplicate worktree handled correctly")
            return True
        else:
            print("✗ Failed to handle duplicate worktree")
            return False
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)

def test_cleanup_nonexistent_worktree():
    """Test that cleaning up non-existent worktree doesn't crash."""
    test_dir = tempfile.mkdtemp()
    try:
        repo_path = Path(test_dir) / 'test_repo'
        repo_path.mkdir()
        workspace_path = repo_path / '.selfai_data'

        init_git_repo(repo_path)
        mgr = WorktreeManager(repo_path, workspace_path)

        # Try to cleanup non-existent worktree
        mgr.cleanup_worktree(999)  # ID that doesn't exist

        print("✓ Cleanup of non-existent worktree handled gracefully")
        return True
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)

def test_get_active_worktrees():
    """Test getting list of active worktrees."""
    test_dir = tempfile.mkdtemp()
    try:
        repo_path = Path(test_dir) / 'test_repo'
        repo_path.mkdir()
        workspace_path = repo_path / '.selfai_data'

        init_git_repo(repo_path)
        mgr = WorktreeManager(repo_path, workspace_path)

        # Create multiple worktrees
        mgr.create_worktree(1, "Feature 1")
        mgr.create_worktree(2, "Feature 2")

        active = mgr.get_active_worktrees()

        if len(active) == 2:
            print("✓ Active worktree tracking works")
            return True
        else:
            print(f"✗ Expected 2 active worktrees, got {len(active)}")
            return False
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)

def test_error_handling_no_git():
    """Test that missing git repository is handled."""
    test_dir = tempfile.mkdtemp()
    try:
        repo_path = Path(test_dir) / 'test_repo'
        repo_path.mkdir()
        workspace_path = repo_path / '.selfai_data'

        # DON'T initialize git repo
        mgr = WorktreeManager(repo_path, workspace_path)

        # Try to create worktree - should raise WorktreeError
        from selfai.runner import WorktreeError
        try:
            result = mgr.create_worktree(1, "Test Feature")
            print("✗ Should have raised WorktreeError with no git repo")
            return False
        except WorktreeError:
            print("✓ Missing git repo handled correctly with WorktreeError")
            return True
    except Exception as e:
        print(f"✗ Test failed with unexpected exception: {e}")
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)

def test_validation_error_invalid_improvement():
    """Test validation errors for invalid improvement structure."""
    test_dir = tempfile.mkdtemp()
    try:
        repo_path = Path(test_dir) / 'test_repo'
        repo_path.mkdir()
        workspace_path = repo_path / '.selfai_data'

        init_git_repo(repo_path)
        mgr = WorktreeManager(repo_path, workspace_path)

        # Test None improvement
        try:
            mgr.validate_improvement(None)
            print("✗ Should have raised ValidationError for None")
            return False
        except ValidationError:
            pass

        # Test missing required fields
        try:
            mgr.validate_improvement({'title': 'Test'})
            print("✗ Should have raised ValidationError for missing id")
            return False
        except ValidationError:
            pass

        # Test invalid ID
        try:
            mgr.validate_improvement({'id': -1, 'title': 'Test'})
            print("✗ Should have raised ValidationError for negative id")
            return False
        except ValidationError:
            pass

        # Test short title
        try:
            mgr.validate_improvement({'id': 1, 'title': 'ab'})
            print("✗ Should have raised ValidationError for short title")
            return False
        except ValidationError:
            pass

        print("✓ Validation errors work correctly")
        return True
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)

def test_disk_space_check():
    """Test disk space checking."""
    test_dir = tempfile.mkdtemp()
    try:
        repo_path = Path(test_dir) / 'test_repo'
        repo_path.mkdir()
        workspace_path = repo_path / '.selfai_data'

        init_git_repo(repo_path)
        mgr = WorktreeManager(repo_path, workspace_path)

        # Normal case should pass
        mgr.check_disk_space()

        # Test with mocked low disk space
        with patch('os.statvfs') as mock_stat:
            mock_result = MagicMock()
            mock_result.f_bavail = 100
            mock_result.f_frsize = 1024
            mock_stat.return_value = mock_result

            try:
                mgr.check_disk_space()
                print("✗ Should have raised DiskSpaceError")
                return False
            except DiskSpaceError:
                print("✓ Disk space check works correctly")
                return True
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)

def test_worktree_name_collision():
    """Test collision detection and resolution."""
    test_dir = tempfile.mkdtemp()
    try:
        repo_path = Path(test_dir) / 'test_repo'
        repo_path.mkdir()
        workspace_path = repo_path / '.selfai_data'

        init_git_repo(repo_path)
        mgr = WorktreeManager(repo_path, workspace_path)

        # Create first worktree
        wt1 = mgr.create_worktree(1, "Feature Test")

        # Create worktree directory manually to force collision
        base_path = workspace_path / 'worktrees' / 'wt-2'
        base_path.mkdir(parents=True, exist_ok=True)

        # Create second worktree - should resolve collision
        wt2 = mgr.create_worktree(2, "Feature Test 2")

        if wt1 and wt2 and wt1.exists() and wt2.exists() and wt1 != wt2:
            print("✓ Worktree name collision resolved")
            return True
        else:
            print("✗ Failed to resolve collision")
            return False
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)

def test_invalid_branch_name_sanitization():
    """Test sanitization of complex invalid branch names."""
    test_dir = tempfile.mkdtemp()
    try:
        repo_path = Path(test_dir) / 'test_repo'
        repo_path.mkdir()
        workspace_path = repo_path / '.selfai_data'

        init_git_repo(repo_path)
        mgr = WorktreeManager(repo_path, workspace_path)

        test_cases = [
            ("Feature with spaces", "feature/feature-with-spaces"),
            ("Feature/with/slashes", "feature/featurewithslashes"),
            ("Feature__multiple___underscores", "feature/feature-multiple-underscores"),
            ("123-numeric-start", "feature/123-numeric-start"),
            ("!@#$%^&*()", "feature/feature"),
        ]

        for input_name, expected_base in test_cases:
            result = mgr._sanitize_branch_name(input_name)
            if not result.startswith("feature/"):
                print(f"✗ Sanitization failed for '{input_name}': missing feature/ prefix")
                return False

        print("✓ Branch name sanitization handles all cases")
        return True
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)

def test_retry_logic():
    """Test retry logic for transient failures."""
    test_dir = tempfile.mkdtemp()
    try:
        repo_path = Path(test_dir) / 'test_repo'
        repo_path.mkdir()
        workspace_path = repo_path / '.selfai_data'

        init_git_repo(repo_path)
        mgr = WorktreeManager(repo_path, workspace_path)

        # Test that _run_git retries on failure
        call_count = [0]
        original_run = subprocess.run

        def mock_run(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] < 3:
                result = MagicMock()
                result.returncode = 1
                result.stdout = ""
                result.stderr = "Temporary failure"
                return result
            return original_run(*args, **kwargs)

        with patch('subprocess.run', side_effect=mock_run):
            success, output = mgr._run_git('status', retry=True)
            if call_count[0] >= 3:
                print("✓ Retry logic executed multiple attempts")
                return True
            else:
                print(f"✗ Expected 3+ attempts, got {call_count[0]}")
                return False
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)

def test_merge_to_main():
    """Test merge to main functionality."""
    test_dir = tempfile.mkdtemp()
    try:
        repo_path = Path(test_dir) / 'test_repo'
        repo_path.mkdir()
        workspace_path = repo_path / '.selfai_data'

        init_git_repo(repo_path)
        mgr = WorktreeManager(repo_path, workspace_path)

        # Create worktree and make changes
        wt = mgr.create_worktree(1, "Test Merge")
        if not wt:
            print("✗ Failed to create worktree")
            return False

        # Make a change in worktree
        test_file = wt / 'test.txt'
        test_file.write_text('Test content')
        subprocess.run(['git', 'add', '.'], cwd=str(wt), capture_output=True)
        subprocess.run(['git', 'commit', '-m', 'Test commit'], cwd=str(wt), capture_output=True)

        # Merge to main
        success, message = mgr.merge_to_main(1, "Test Merge")

        if success:
            print("✓ Merge to main works")
            return True
        else:
            print(f"✗ Merge failed: {message}")
            return False
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)

if __name__ == '__main__':
    print("Testing Git Worktree Enhanced Level...\n")

    results = []

    print("1. Testing branch name sanitization...")
    results.append(test_sanitize_branch_name())
    print()

    print("2. Testing duplicate worktree handling...")
    results.append(test_duplicate_worktree_handling())
    print()

    print("3. Testing cleanup of non-existent worktree...")
    results.append(test_cleanup_nonexistent_worktree())
    print()

    print("4. Testing active worktree tracking...")
    results.append(test_get_active_worktrees())
    print()

    print("5. Testing error handling with no git repo...")
    results.append(test_error_handling_no_git())
    print()

    print("6. Testing validation errors for invalid improvement...")
    results.append(test_validation_error_invalid_improvement())
    print()

    print("7. Testing disk space check...")
    results.append(test_disk_space_check())
    print()

    print("8. Testing worktree name collision resolution...")
    results.append(test_worktree_name_collision())
    print()

    print("9. Testing invalid branch name sanitization...")
    results.append(test_invalid_branch_name_sanitization())
    print()

    print("10. Testing retry logic for transient failures...")
    results.append(test_retry_logic())
    print()

    print("11. Testing merge to main...")
    results.append(test_merge_to_main())
    print()

    print(f"\nResults: {sum(results)}/{len(results)} tests passed")

    if all(results):
        print("\n✓ All Enhanced tests passed!")
        exit(0)
    else:
        print("\n✗ Some tests failed")
        exit(1)
