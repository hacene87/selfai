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
    if (cwd / 'selfai').exists():
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
        <string>selfai</string>
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


def analyze_logs():
    """Analyze recent logs and display issues found."""
    repo_path = get_repo_root()
    runner = Runner(repo_path)

    print("\n=== Log Analysis ===")
    print(f"Repository: {repo_path}")

    # Run analysis
    analysis = runner.log_analyzer.analyze_logs()

    print(f"\nLog Summary:")
    print(f"  Lines analyzed: {analysis.get('log_lines', 0)}")
    print(f"  Issues found:   {analysis.get('issues_found', 0)}")

    # Display issues
    issues = analysis.get('issues', [])
    if issues:
        print(f"\nRecent Issues:")
        for i, issue in enumerate(issues, 1):
            issue_type = issue.get('type', 'unknown').upper()
            detail = issue.get('detail', 'No details')[:80]
            timestamp = issue.get('timestamp', '')[:19]
            print(f"  {i}. [{issue_type}] {detail}")
            print(f"     Time: {timestamp}")
    else:
        print("\n  No issues detected in recent logs.")

    # Show log file location
    log_file = runner.log_analyzer.logs_path / 'runner.log'
    if log_file.exists():
        print(f"\nLog file: {log_file}")

    print()


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


def test_feature(feature_id: int, verbose: bool = False):
    """Test a specific feature by ID."""
    repo_path = get_repo_root()
    runner = Runner(repo_path)

    can_test, reason = runner.db.can_test_feature(feature_id)
    if not can_test:
        print(f"Cannot test feature #{feature_id}: {reason}")
        return

    feature = runner.db.get_by_id(feature_id)
    if not feature:
        print(f"Feature #{feature_id} not found")
        return

    retry_count = feature.get('retry_count', 0)
    level = feature.get('current_level', 1)
    level_name = {1: 'MVP', 2: 'Enhanced', 3: 'Advanced'}[level]

    print(f"\nTesting Feature #{feature_id}")
    print(f"  Title: {feature['title']}")
    print(f"  Level: {level_name} ({level}/3)")
    print(f"  Retry: {retry_count}/3")
    print(f"  Status: {feature['status']}")

    if verbose:
        print(f"\n  Description: {feature.get('description', 'N/A')}")
        print(f"  Category: {feature.get('category', 'N/A')}")
        print(f"  Priority: {feature.get('priority', 'N/A')}")

    print("\nRunning tests...")

    try:
        runner._run_tests(feature)

        updated = runner.db.get_by_id(feature_id)
        if updated:
            level_col = {1: 'mvp', 2: 'enhanced', 3: 'advanced'}[level]
            test_status = updated.get(f'{level_col}_test_status', 'pending')

            print(f"\nTest Result: {test_status.upper()}")

            if verbose and updated.get(f'{level_col}_test_output'):
                print(f"\nTest Output:")
                print(updated.get(f'{level_col}_test_output')[:500])
        else:
            print("Error: Could not retrieve updated feature status")

    except Exception as e:
        print(f"Error running tests: {e}")


def print_help():
    """Print usage help."""
    print("""
SelfAI - Autonomous Self-Improving System with MVP Testing & Progressive Complexity

Usage:
    python -m _selfai <command> [options]

Commands:
    install      Install LaunchAgent (runs every 5 minutes)
    uninstall    Remove LaunchAgent
    run          Run a single improvement cycle (includes testing)
    status       Show current status with complexity & test stats
    dashboard    Open dashboard in browser
    analyze-logs Analyze recent logs for errors and issues
    add          Add a manual improvement
    test         Test a specific feature by ID
    help         Show this help

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
    python -m _selfai status                   # Check progress & test status
    python -m _selfai dashboard                # View in browser
    python -m _selfai analyze-logs             # Check for errors in logs
    python -m _selfai add "Fix bug X"          # Add MVP-level task
    python -m _selfai test 5                   # Test feature #5
    python -m _selfai test 5 --verbose         # Test feature #5 with detailed output
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
    elif command == 'status':
        show_status()
    elif command == 'dashboard':
        open_dashboard()
    elif command == 'analyze-logs':
        analyze_logs()
    elif command == 'add':
        if len(sys.argv) < 3:
            print("Usage: python -m _selfai add \"title\" [description] [category] [priority]")
            return
        title = sys.argv[2]
        description = sys.argv[3] if len(sys.argv) > 3 else ''
        category = sys.argv[4] if len(sys.argv) > 4 else 'general'
        priority = int(sys.argv[5]) if len(sys.argv) > 5 else 50
        add_improvement(title, description, category, priority)
    elif command == 'test':
        if len(sys.argv) < 3:
            print("Usage: python -m _selfai test <feature_id> [--verbose]")
            return
        try:
            feature_id = int(sys.argv[2])
            verbose = '--verbose' in sys.argv or '-v' in sys.argv
            test_feature(feature_id, verbose)
        except ValueError:
            print("Error: feature_id must be an integer")
    elif command in ('help', '-h', '--help'):
        print_help()
    else:
        print(f"Unknown command: {command}")
        print_help()


if __name__ == '__main__':
    main()
