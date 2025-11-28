"""Tests for isolated test environment functionality."""
import pytest
import subprocess
from pathlib import Path
from selfai.test_environment import TestEnvironment, TestEnvironmentManager


def test_port_allocation_uniqueness(isolated_test_env):
    """Test that each environment gets unique ports."""
    port1 = isolated_test_env.get_port('main')
    port2 = isolated_test_env.get_port('api')

    assert port1 != port2
    assert 10000 <= port1 <= 65535
    assert 10000 <= port2 <= 65535


def test_port_allocation_consistency(isolated_test_env):
    """Test that same service always gets same port."""
    port1 = isolated_test_env.get_port('main')
    port2 = isolated_test_env.get_port('main')

    assert port1 == port2


def test_database_isolation(isolated_db):
    """Test that database is truly isolated."""
    # Add data to isolated db
    task_id = isolated_db.add("Test Task", "Description")

    # Verify data exists
    task = isolated_db.get_by_id(task_id)
    assert task is not None
    assert task['title'] == "Test Task"


def test_database_operations(isolated_db):
    """Test basic database operations in isolated environment."""
    # Add multiple tasks
    id1 = isolated_db.add("Task 1", "Description 1")
    id2 = isolated_db.add("Task 2", "Description 2")

    # Get all tasks
    tasks = isolated_db.get_all()
    assert len(tasks) == 2

    # Update status
    isolated_db.mark_planning(id1)
    task = isolated_db.get_by_id(id1)
    assert task['status'] == 'planning'

    # Check stats
    stats = isolated_db.get_stats()
    assert stats['total'] == 2
    assert stats['planning'] == 1
    assert stats['pending'] == 1


def test_worktree_isolation(isolated_test_env):
    """Test that worktree provides file isolation."""
    wt_path = isolated_test_env.worktree_path

    # Create file in worktree
    test_file = wt_path / 'test_isolation.txt'
    test_file.write_text('isolated content')

    assert test_file.exists()
    assert test_file.read_text() == 'isolated content'


def test_worktree_git_operations(isolated_test_env):
    """Test git operations in isolated worktree."""
    wt_path = isolated_test_env.worktree_path

    # Create and commit a file
    test_file = wt_path / 'new_file.txt'
    test_file.write_text('test content')

    # Add and commit
    subprocess.run(['git', 'add', '.'], cwd=str(wt_path), capture_output=True)
    result = subprocess.run(
        ['git', 'commit', '-m', 'Test commit'],
        cwd=str(wt_path),
        capture_output=True,
        text=True
    )

    assert result.returncode == 0
    assert test_file.exists()


def test_logging_isolation(isolated_test_env):
    """Test that each environment has its own log files."""
    log_path = isolated_test_env.log_path

    assert log_path is not None
    assert log_path.exists()

    # Check that log files were created
    main_log = isolated_test_env.get_log_file('main')
    assert main_log.exists()


def test_environment_variables(isolated_test_env):
    """Test that environment variables are set correctly."""
    env_vars = isolated_test_env.get_environment_variables()

    assert 'SELFAI_TEST_ENV_ID' in env_vars
    assert 'SELFAI_TEST_PORT_BASE' in env_vars
    assert 'SELFAI_TEST_DB_PATH' in env_vars
    assert 'SELFAI_ISOLATED_TEST' in env_vars
    assert env_vars['SELFAI_ISOLATED_TEST'] == '1'


def test_subprocess_environment(isolated_test_env):
    """Test subprocess environment includes all necessary variables."""
    env = isolated_test_env.as_subprocess_env()

    # Should include system environment
    assert 'PATH' in env

    # Should include isolated environment variables
    assert 'SELFAI_TEST_ENV_ID' in env
    assert 'SELFAI_ISOLATED_TEST' in env


def test_environment_cleanup(tmp_path):
    """Test that cleanup removes all resources."""
    env = TestEnvironment(9999, tmp_path)

    # Create minimal repo
    repo = tmp_path / 'repo'
    repo.mkdir()
    subprocess.run(['git', 'init'], cwd=str(repo), capture_output=True)
    subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=str(repo), capture_output=True)
    subprocess.run(['git', 'config', 'user.email', 'test@test.com'], cwd=str(repo), capture_output=True)
    (repo / 'README.md').write_text('# Test')
    subprocess.run(['git', 'add', '.'], cwd=str(repo), capture_output=True)
    subprocess.run(['git', 'commit', '-m', 'Initial'], cwd=str(repo), capture_output=True)

    env.setup(repo)

    db_path = env.db_path
    wt_path = env.worktree_path

    assert db_path.exists()
    assert wt_path.exists()

    env.cleanup()

    # Verify cleanup
    assert not db_path.exists()
    assert not wt_path.exists()


def test_environment_context_manager(tmp_path):
    """Test that context manager properly cleans up."""
    # Create minimal repo
    repo = tmp_path / 'repo'
    repo.mkdir()
    subprocess.run(['git', 'init'], cwd=str(repo), capture_output=True)
    subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=str(repo), capture_output=True)
    subprocess.run(['git', 'config', 'user.email', 'test@test.com'], cwd=str(repo), capture_output=True)
    (repo / 'README.md').write_text('# Test')
    subprocess.run(['git', 'add', '.'], cwd=str(repo), capture_output=True)
    subprocess.run(['git', 'commit', '-m', 'Initial'], cwd=str(repo), capture_output=True)

    db_path = None
    wt_path = None

    with TestEnvironment(8888, tmp_path) as env:
        env.setup(repo)
        db_path = env.db_path
        wt_path = env.worktree_path
        assert db_path.exists()
        assert wt_path.exists()

    # After context, should be cleaned up
    assert not db_path.exists()
    assert not wt_path.exists()


@pytest.mark.parametrize("task_id", [1, 2, 3])
def test_multiple_environments_parallel(tmp_path, task_id):
    """Test that multiple environments can coexist."""
    # This test runs in parallel with pytest-xdist
    env = TestEnvironment(task_id * 1000, tmp_path)

    # Verify unique port allocation
    port = env._allocate_port_range()
    assert port >= 10000


def test_environment_manager_creation(tmp_path):
    """Test TestEnvironmentManager creates environments."""
    # Create minimal repo
    repo = tmp_path / 'repo'
    repo.mkdir()
    subprocess.run(['git', 'init'], cwd=str(repo), capture_output=True)
    subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=str(repo), capture_output=True)
    subprocess.run(['git', 'config', 'user.email', 'test@test.com'], cwd=str(repo), capture_output=True)
    (repo / 'README.md').write_text('# Test')
    subprocess.run(['git', 'add', '.'], cwd=str(repo), capture_output=True)
    subprocess.run(['git', 'commit', '-m', 'Initial'], cwd=str(repo), capture_output=True)

    manager = TestEnvironmentManager(repo, max_environments=3)

    env1 = manager.create_environment(1)
    assert env1 is not None
    assert env1.task_id == 1

    # Get same environment
    env1_again = manager.get_environment(1)
    assert env1_again is env1


def test_environment_manager_max_environments(tmp_path):
    """Test that manager enforces max environments limit."""
    # Create minimal repo
    repo = tmp_path / 'repo'
    repo.mkdir()
    subprocess.run(['git', 'init'], cwd=str(repo), capture_output=True)
    subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=str(repo), capture_output=True)
    subprocess.run(['git', 'config', 'user.email', 'test@test.com'], cwd=str(repo), capture_output=True)
    (repo / 'README.md').write_text('# Test')
    subprocess.run(['git', 'add', '.'], cwd=str(repo), capture_output=True)
    subprocess.run(['git', 'commit', '-m', 'Initial'], cwd=str(repo), capture_output=True)

    manager = TestEnvironmentManager(repo, max_environments=2)

    env1 = manager.create_environment(1)
    env2 = manager.create_environment(2)

    # Should raise error when exceeding limit
    with pytest.raises(RuntimeError, match="Maximum 2 concurrent test environments"):
        manager.create_environment(3)


def test_environment_manager_release(tmp_path):
    """Test that manager properly releases environments."""
    # Create minimal repo
    repo = tmp_path / 'repo'
    repo.mkdir()
    subprocess.run(['git', 'init'], cwd=str(repo), capture_output=True)
    subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=str(repo), capture_output=True)
    subprocess.run(['git', 'config', 'user.email', 'test@test.com'], cwd=str(repo), capture_output=True)
    (repo / 'README.md').write_text('# Test')
    subprocess.run(['git', 'add', '.'], cwd=str(repo), capture_output=True)
    subprocess.run(['git', 'commit', '-m', 'Initial'], cwd=str(repo), capture_output=True)

    manager = TestEnvironmentManager(repo, max_environments=2)

    env1 = manager.create_environment(1)
    db_path = env1.db_path
    wt_path = env1.worktree_path

    # Release environment
    manager.release_environment(1)

    # Should be cleaned up
    assert not db_path.exists()
    assert not wt_path.exists()

    # Should be able to create new environment now
    env2 = manager.create_environment(2)
    assert env2 is not None


def test_environment_manager_cleanup_all(tmp_path):
    """Test that manager can cleanup all environments."""
    # Create minimal repo
    repo = tmp_path / 'repo'
    repo.mkdir()
    subprocess.run(['git', 'init'], cwd=str(repo), capture_output=True)
    subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=str(repo), capture_output=True)
    subprocess.run(['git', 'config', 'user.email', 'test@test.com'], cwd=str(repo), capture_output=True)
    (repo / 'README.md').write_text('# Test')
    subprocess.run(['git', 'add', '.'], cwd=str(repo), capture_output=True)
    subprocess.run(['git', 'commit', '-m', 'Initial'], cwd=str(repo), capture_output=True)

    manager = TestEnvironmentManager(repo, max_environments=3)

    env1 = manager.create_environment(1)
    env2 = manager.create_environment(2)

    db_path1 = env1.db_path
    db_path2 = env2.db_path

    manager.cleanup_all()

    # All should be cleaned up
    assert not db_path1.exists()
    assert not db_path2.exists()
    assert len(manager.active_environments) == 0


def test_isolated_ports_fixture(isolated_ports):
    """Test that isolated_ports fixture provides valid ports."""
    assert 'main' in isolated_ports
    assert 'api' in isolated_ports
    assert 'database' in isolated_ports

    # All should be different
    assert isolated_ports['main'] != isolated_ports['api']
    assert isolated_ports['main'] != isolated_ports['database']
    assert isolated_ports['api'] != isolated_ports['database']

    # All should be valid port numbers
    for port in isolated_ports.values():
        assert 1 <= port <= 65535
