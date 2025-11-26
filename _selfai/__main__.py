"""CLI entry point for SelfAI."""
import sys
import os
import webbrowser
import subprocess
from pathlib import Path

from .runner import Runner


def get_repo_root() -> Path:
    """Get the repository root (parent of .selfai folder)."""
    # Use current working directory to respect LaunchAgent's WorkingDirectory
    cwd = Path.cwd()
    if (cwd / '_selfai').exists():
        return cwd
    # Fall back to __file__ location for direct execution
    return Path(__file__).parent.parent.resolve()


def install_launchagent():
    """Install macOS LaunchAgent for scheduled runs."""
    repo_path = get_repo_root()
    workspace_path = repo_path / '.selfai_data'

    # Ensure workspace logs directory exists
    (workspace_path / 'logs').mkdir(parents=True, exist_ok=True)

    # LaunchAgent configuration
    label = f"com.selfai.{repo_path.name}"
    plist_path = Path.home() / 'Library' / 'LaunchAgents' / f'{label}.plist'

    python_path = sys.executable

    plist_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_path}</string>
        <string>-m</string>
        <string>_selfai</string>
        <string>run</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{repo_path}</string>
    <key>StartInterval</key>
    <integer>180</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{workspace_path}/logs/launchd.log</string>
    <key>StandardErrorPath</key>
    <string>{workspace_path}/logs/launchd_error.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
        <key>PYTHONPATH</key>
        <string>{repo_path}</string>
    </dict>
</dict>
</plist>'''

    # Ensure LaunchAgents directory exists
    plist_path.parent.mkdir(parents=True, exist_ok=True)

    # Unload existing if present
    try:
        subprocess.run(['launchctl', 'unload', str(plist_path)],
                      capture_output=True, check=False)
    except Exception:
        pass

    # Write plist
    plist_path.write_text(plist_content)
    print(f"Created LaunchAgent: {plist_path}")

    # Load the agent
    result = subprocess.run(['launchctl', 'load', str(plist_path)],
                           capture_output=True, text=True)

    if result.returncode == 0:
        print(f"LaunchAgent installed and started!")
        print(f"  - Runs every 3 minutes")
        print(f"  - Repository: {repo_path}")
        print(f"  - Workspace: {workspace_path}")
    else:
        print(f"Failed to load LaunchAgent: {result.stderr}")
        return False

    return True


def uninstall_launchagent():
    """Uninstall the LaunchAgent."""
    repo_path = get_repo_root()
    label = f"com.selfai.{repo_path.name}"
    plist_path = Path.home() / 'Library' / 'LaunchAgents' / f'{label}.plist'

    if plist_path.exists():
        subprocess.run(['launchctl', 'unload', str(plist_path)],
                      capture_output=True, check=False)
        plist_path.unlink()
        print(f"LaunchAgent uninstalled: {label}")
    else:
        print("No LaunchAgent found for this repository")


def show_status():
    """Show current status with 3-level progression."""
    repo_path = get_repo_root()
    runner = Runner(repo_path)
    stats = runner.get_status()
    level_stats = runner.db.get_level_stats()

    print("\n=== SelfAI Status ===")
    print(f"Repository: {repo_path}")

    print(f"\nFeatures:")
    print(f"  Pending:     {stats.get('pending', 0)}")
    print(f"  In Progress: {stats.get('in_progress', 0)}")
    print(f"  Testing:     {stats.get('testing', 0)}")
    print(f"  Completed:   {stats.get('completed', 0)} (all 3 levels done)")
    print(f"  Total:       {stats.get('total', 0)}")

    print(f"\nLevel Progress:")
    for level, name in [(1, 'MVP'), (2, 'Enhanced'), (3, 'Advanced')]:
        lvl = level_stats.get(level, {})
        passed = lvl.get('passed', 0)
        in_prog = lvl.get('in_progress', 0)
        print(f"  {name}: {passed} passed, {in_prog} in progress")

    # Check if LaunchAgent is installed
    label = f"com.selfai.{repo_path.name}"
    plist_path = Path.home() / 'Library' / 'LaunchAgents' / f'{label}.plist'

    if plist_path.exists():
        print(f"\nLaunchAgent: Installed (runs every 3 minutes)")
    else:
        print(f"\nLaunchAgent: Not installed (run 'python -m _selfai install')")


def open_dashboard():
    """Open the dashboard in browser."""
    repo_path = get_repo_root()
    dashboard_path = repo_path / '.selfai_data' / 'dashboard.html'

    if not dashboard_path.exists():
        # Generate dashboard first
        runner = Runner(repo_path)
        runner.update_dashboard()

    webbrowser.open(f'file://{dashboard_path}')
    print(f"Opened dashboard: {dashboard_path}")


def run_once():
    """Run a single improvement cycle."""
    repo_path = get_repo_root()
    print(f"Running SelfAI for: {repo_path}")

    runner = Runner(repo_path)
    runner.run_once()

    stats = runner.get_status()
    print(f"\nStatus: {stats['completed']} completed, {stats['in_progress']} in progress, {stats['pending']} pending")


def add_improvement(title: str, description: str = '', category: str = 'general',
                    priority: int = 50):
    """Add a manual improvement (starts at MVP level)."""
    repo_path = get_repo_root()
    runner = Runner(repo_path)

    imp_id = runner.db.add(
        title=title,
        description=description,
        category=category,
        priority=priority,
        source='manual'
    )

    runner.update_dashboard()
    print(f"Added improvement #{imp_id}: {title}")


def test_feature(feature_id: int):
    """Run tests for a specific feature by ID."""
    repo_path = get_repo_root()
    runner = Runner(repo_path)

    # Get the feature
    improvement = runner.db.get_by_id(feature_id)
    if not improvement:
        print(f"Error: Feature #{feature_id} not found")
        return False

    title = improvement['title']
    level = improvement['current_level']
    level_name = {1: 'MVP', 2: 'Enhanced', 3: 'Advanced'}[level]
    status = improvement['status']

    print(f"\n=== Testing Feature #{feature_id} ===")
    print(f"Title: {title}")
    print(f"Level: {level_name} ({level}/3)")
    print(f"Status: {status}")
    print(f"\nTest Criteria for {level_name}:")
    print(runner._get_test_criteria(level))
    print("\nRunning tests...")

    # Run the tests
    runner._run_tests(improvement)

    # Get updated status
    updated = runner.db.get_by_id(feature_id)
    level_col = level_name.lower()
    test_status = updated.get(f'{level_col}_test_status', 'unknown')
    test_output = updated.get(f'{level_col}_test_output', '')

    print(f"\n=== Test Results ===")
    print(f"Status: {test_status.upper()}")

    if test_status == 'passed':
        print(f"Feature #{feature_id} passed {level_name} tests!")
        print(f"New status: {updated['status']}")
    else:
        print(f"Feature #{feature_id} failed {level_name} tests")
        print(f"Retry count: {updated['retry_count']}")

    if test_output:
        print(f"\nTest Output (truncated):")
        print(test_output[:500] + '...' if len(test_output) > 500 else test_output)

    runner.update_dashboard()
    return test_status == 'passed'


def print_help():
    """Print usage help."""
    print("""
SelfAI - Autonomous Self-Improving System with MVP Testing & Progressive Complexity

Usage:
    python -m _selfai <command> [options]

Commands:
    install     Install LaunchAgent (runs every 5 minutes)
    uninstall   Remove LaunchAgent
    run         Run a single improvement cycle (includes testing)
    test <id>   Run tests for a specific feature by ID
    status      Show current status with complexity & test stats
    dashboard   Open dashboard in browser
    add         Add a manual improvement
    help        Show this help

Complexity Levels:
    1 = MVP       (Simple, working implementations)
    2 = Enhanced  (Robust with edge cases, unlocked after 5 tested MVP features)
    3 = Advanced  (Production-ready, unlocked after 10 tested Enhanced features)

Run Cycle:
    1. Resume stuck tasks (if any)
    2. Test completed features (MVP validation)
    3. Process pending improvements (respecting complexity level)
    4. Discover new improvements (if queue empty)

Examples:
    python -m _selfai install                  # Start autonomous improvements
    python -m _selfai run                      # Run once manually
    python -m _selfai test 5                   # Test feature #5 manually
    python -m _selfai status                   # Check progress & test status
    python -m _selfai dashboard                # View in browser
    python -m _selfai add "Fix bug X"          # Add MVP-level task
    python -m _selfai add "Feature" "" "" 80 2 # Add Enhanced task (priority 80)
    python -m _selfai uninstall                # Stop autonomous runs
""")


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print_help()
        return

    command = sys.argv[1].lower()

    if command == 'install':
        install_launchagent()
    elif command == 'uninstall':
        uninstall_launchagent()
    elif command == 'run':
        run_once()
    elif command == 'test':
        if len(sys.argv) < 3:
            print("Usage: python -m _selfai test <feature_id>")
            return
        try:
            feature_id = int(sys.argv[2])
        except ValueError:
            print("Error: Feature ID must be an integer")
            return
        test_feature(feature_id)
    elif command == 'status':
        show_status()
    elif command == 'dashboard':
        open_dashboard()
    elif command == 'add':
        if len(sys.argv) < 3:
            print("Usage: python -m _selfai add \"title\" [description] [category] [priority]")
            return
        title = sys.argv[2]
        description = sys.argv[3] if len(sys.argv) > 3 else ''
        category = sys.argv[4] if len(sys.argv) > 4 else 'general'
        priority = int(sys.argv[5]) if len(sys.argv) > 5 else 50
        add_improvement(title, description, category, priority)
    elif command in ('help', '-h', '--help'):
        print_help()
    else:
        print(f"Unknown command: {command}")
        print_help()


if __name__ == '__main__':
    main()
