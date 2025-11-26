"""Manual test for WorktreeManager MVP functionality."""
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

def test_worktree_creation():
    """Test creating a worktree."""
    test_dir = tempfile.mkdtemp()
    try:
        repo_path = Path(test_dir) / 'test_repo'
        repo_path.mkdir()
        workspace_path = repo_path / '.selfai_data'

        # Initialize git repo
        init_git_repo(repo_path)

        # Create WorktreeManager
        mgr = WorktreeManager(repo_path, workspace_path)

        # Test creating a worktree
        worktree_path = mgr.create_worktree(1, "Test Feature")

        if worktree_path and worktree_path.exists():
            print("✓ Worktree created successfully")
            return True
        else:
            print("✗ Failed to create worktree")
            return False
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)

def test_worktree_cleanup():
    """Test cleaning up a worktree."""
    test_dir = tempfile.mkdtemp()
    try:
        repo_path = Path(test_dir) / 'test_repo'
        repo_path.mkdir()
        workspace_path = repo_path / '.selfai_data'

        # Initialize git repo
        init_git_repo(repo_path)

        # Create WorktreeManager
        mgr = WorktreeManager(repo_path, workspace_path)

        # Create and then cleanup worktree
        worktree_path = mgr.create_worktree(1, "Test Feature")
        if not worktree_path:
            print("✗ Failed to create worktree for cleanup test")
            return False

        mgr.cleanup_worktree(1)

        if not worktree_path.exists():
            print("✓ Worktree cleaned up successfully")
            return True
        else:
            print("✗ Failed to cleanup worktree")
            return False
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)

def test_parallel_processing_setup():
    """Test that ThreadPoolExecutor is used correctly."""
    from concurrent.futures import ThreadPoolExecutor
    from selfai.runner import Runner

    test_dir = tempfile.mkdtemp()
    try:
        repo_path = Path(test_dir) / 'test_repo'
        repo_path.mkdir()

        # Initialize git repo
        init_git_repo(repo_path)

        runner = Runner(repo_path)

        # Check MAX_WORKERS is set to 3
        if runner.MAX_WORKERS == 3:
            print("✓ MAX_WORKERS is correctly set to 3")
            return True
        else:
            print(f"✗ MAX_WORKERS is {runner.MAX_WORKERS}, expected 3")
            return False
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)

if __name__ == '__main__':
    print("Testing Git Worktree Parallel Processing MVP...\n")

    results = []

    print("1. Testing worktree creation...")
    results.append(test_worktree_creation())
    print()

    print("2. Testing worktree cleanup...")
    results.append(test_worktree_cleanup())
    print()

    print("3. Testing parallel processing setup...")
    results.append(test_parallel_processing_setup())
    print()

    print(f"\nResults: {sum(results)}/{len(results)} tests passed")

    if all(results):
        print("\n✓ All MVP tests passed!")
        exit(0)
    else:
        print("\n✗ Some tests failed")
        exit(1)
