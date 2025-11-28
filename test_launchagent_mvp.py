"""MVP tests for macOS LaunchAgent scheduled execution feature."""
import tempfile
import shutil
from pathlib import Path
import sys
import subprocess
from unittest.mock import patch, MagicMock
import xml.etree.ElementTree as ET

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from selfai.__main__ import install_launchagent, uninstall_launchagent, get_repo_root


def test_install_launchagent_creates_plist():
    """Test that install_launchagent creates a valid plist file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Setup mock paths
        repo_path = Path(tmpdir) / 'test_repo'
        repo_path.mkdir()
        (repo_path / 'selfai').mkdir()

        home_dir = Path(tmpdir) / 'home'
        home_dir.mkdir()
        launch_agents_dir = home_dir / 'Library' / 'LaunchAgents'
        launch_agents_dir.mkdir(parents=True)

        with patch('selfai.__main__.get_repo_root', return_value=repo_path):
            with patch('selfai.__main__.Path.home', return_value=home_dir):
                with patch('subprocess.run') as mock_run:
                    # Mock successful launchctl load
                    mock_run.return_value = MagicMock(returncode=0, stderr='')

                    result = install_launchagent()

                    # Verify plist was created
                    expected_plist = launch_agents_dir / f'com.selfai.test_repo.plist'
                    assert expected_plist.exists(), "Plist file should be created"
                    assert result is True, "Installation should succeed"

                    # Verify logs directory was created
                    logs_dir = repo_path / '.selfai_data' / 'logs'
                    assert logs_dir.exists(), "Logs directory should be created"

    print("✓ test_install_launchagent_creates_plist passed")


def test_plist_content_valid():
    """Test that generated plist contains valid XML and required keys."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir) / 'test_repo'
        repo_path.mkdir()
        (repo_path / 'selfai').mkdir()

        home_dir = Path(tmpdir) / 'home'
        home_dir.mkdir()
        launch_agents_dir = home_dir / 'Library' / 'LaunchAgents'
        launch_agents_dir.mkdir(parents=True)

        with patch('selfai.__main__.get_repo_root', return_value=repo_path):
            with patch('selfai.__main__.Path.home', return_value=home_dir):
                with patch('subprocess.run') as mock_run:
                    mock_run.return_value = MagicMock(returncode=0, stderr='')

                    install_launchagent()

                    # Read and parse plist
                    plist_path = launch_agents_dir / 'com.selfai.test_repo.plist'
                    content = plist_path.read_text()

                    # Verify it's valid XML
                    try:
                        tree = ET.fromstring(content)
                    except ET.ParseError as e:
                        raise AssertionError(f"Plist is not valid XML: {e}")

                    # Check required content
                    assert 'com.selfai.test_repo' in content, "Label should be in plist"
                    assert str(sys.executable) in content, "Python path should be in plist"
                    assert str(repo_path) in content, "WorkingDirectory should be in plist"
                    assert '<integer>180</integer>' in content, "StartInterval should be 180 seconds (3 min)"
                    assert 'launchd.log' in content, "Log file path should be in plist"
                    assert 'launchd_error.log' in content, "Error log path should be in plist"
                    assert '<true/>' in content, "RunAtLoad should be enabled"

    print("✓ test_plist_content_valid passed")


def test_uninstall_launchagent_removes_plist():
    """Test that uninstall_launchagent removes the plist file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir) / 'test_repo'
        repo_path.mkdir()
        (repo_path / 'selfai').mkdir()

        home_dir = Path(tmpdir) / 'home'
        home_dir.mkdir()
        launch_agents_dir = home_dir / 'Library' / 'LaunchAgents'
        launch_agents_dir.mkdir(parents=True)

        # Create a dummy plist file
        plist_path = launch_agents_dir / 'com.selfai.test_repo.plist'
        plist_path.write_text('<?xml version="1.0"?><plist/>')

        assert plist_path.exists(), "Plist should exist before uninstall"

        with patch('selfai.__main__.get_repo_root', return_value=repo_path):
            with patch('selfai.__main__.Path.home', return_value=home_dir):
                with patch('subprocess.run') as mock_run:
                    uninstall_launchagent()

                    # Verify plist was removed
                    assert not plist_path.exists(), "Plist file should be removed"

                    # Verify launchctl unload was called
                    assert mock_run.called, "subprocess.run should be called for launchctl unload"

    print("✓ test_uninstall_launchagent_removes_plist passed")


def test_logs_directory_created():
    """Test that logs directory is created during installation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir) / 'test_repo'
        repo_path.mkdir()
        (repo_path / 'selfai').mkdir()

        home_dir = Path(tmpdir) / 'home'
        home_dir.mkdir()
        launch_agents_dir = home_dir / 'Library' / 'LaunchAgents'
        launch_agents_dir.mkdir(parents=True)

        logs_dir = repo_path / '.selfai_data' / 'logs'
        assert not logs_dir.exists(), "Logs directory should not exist initially"

        with patch('selfai.__main__.get_repo_root', return_value=repo_path):
            with patch('selfai.__main__.Path.home', return_value=home_dir):
                with patch('subprocess.run') as mock_run:
                    mock_run.return_value = MagicMock(returncode=0, stderr='')

                    install_launchagent()

                    # Verify logs directory exists
                    assert logs_dir.exists(), "Logs directory should be created"
                    assert logs_dir.is_dir(), "Logs path should be a directory"

    print("✓ test_logs_directory_created passed")


if __name__ == '__main__':
    print("\n=== Running LaunchAgent MVP Tests ===\n")

    try:
        test_install_launchagent_creates_plist()
        test_plist_content_valid()
        test_uninstall_launchagent_removes_plist()
        test_logs_directory_created()

        print("\n=== All Tests Passed ===")
        sys.exit(0)
    except AssertionError as e:
        print(f"\n✗ Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
