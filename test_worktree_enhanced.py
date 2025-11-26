"""Enhanced level tests for Git Worktree Parallel Processing.

Tests edge cases, error handling, and input validation.
"""
import tempfile
import shutil
import subprocess
from pathlib import Path
from selfai.runner import WorktreeManager

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

        # Try to create worktree - should fail gracefully
        result = mgr.create_worktree(1, "Test Feature")

        if result is None:
            print("✓ Missing git repo handled correctly")
            return True
        else:
            print("✗ Should have failed with no git repo")
            return False
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
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

    print(f"\nResults: {sum(results)}/{len(results)} tests passed")

    if all(results):
        print("\n✓ All Enhanced tests passed!")
        exit(0)
    else:
        print("\n✗ Some tests failed")
        exit(1)
