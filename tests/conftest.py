"""
Pytest configuration and fixtures for isolated testing.
"""
import pytest
import tempfile
import shutil
import subprocess
from pathlib import Path
from selfai.test_environment import TestEnvironment, TestEnvironmentManager
from selfai.database import Database


@pytest.fixture
def isolated_test_env(tmp_path, request):
    """Provide isolated test environment for each test."""
    # Use pytest worker_id for parallel execution
    worker_id = getattr(request.config, 'workerinput', {}).get('workerid', 'master')
    task_id = hash(f"{worker_id}-{request.node.name}") % 10000

    env = TestEnvironment(task_id, tmp_path)

    # Create minimal git repo for worktree
    repo_path = tmp_path / 'repo'
    repo_path.mkdir()
    subprocess.run(['git', 'init'], cwd=str(repo_path), capture_output=True)
    subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=str(repo_path), capture_output=True)
    subprocess.run(['git', 'config', 'user.email', 'test@test.com'], cwd=str(repo_path), capture_output=True)
    (repo_path / 'README.md').write_text('# Test')
    subprocess.run(['git', 'add', '.'], cwd=str(repo_path), capture_output=True)
    subprocess.run(['git', 'commit', '-m', 'Initial'], cwd=str(repo_path), capture_output=True)

    env.setup(repo_path)
    yield env
    env.cleanup()


@pytest.fixture
def isolated_db(isolated_test_env):
    """Provide isolated database instance."""
    return isolated_test_env.get_database()


@pytest.fixture
def isolated_ports(isolated_test_env):
    """Provide isolated port allocation."""
    return {
        'main': isolated_test_env.get_port('main'),
        'api': isolated_test_env.get_port('api'),
        'database': isolated_test_env.get_port('database'),
    }
