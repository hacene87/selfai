"""CLI entry point for SelfAI - Planning-First Workflow."""
import sys
import os
import webbrowser
import subprocess
from pathlib import Path

from .runner import SelfAIRunner
from .server import run_server
from .healers import KnowledgeBase


def get_repo_root() -> Path:
    """Get the repository root."""
    cwd = Path.cwd()
    if (cwd / 'selfai').exists():
        return cwd
    return Path(__file__).parent.parent.resolve()


def install_launchagent():
    """Install macOS LaunchAgent for scheduled runs."""
    repo_path = get_repo_root()
    workspace_path = repo_path / '.selfai_data'
    (workspace_path / 'logs').mkdir(parents=True, exist_ok=True)

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

    plist_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        subprocess.run(['launchctl', 'unload', str(plist_path)],
                      capture_output=True, check=False)
    except Exception:
        pass

    plist_path.write_text(plist_content)
    print(f"Created LaunchAgent: {plist_path}")

    result = subprocess.run(['launchctl', 'load', str(plist_path)],
                           capture_output=True, text=True)

    if result.returncode == 0:
        print(f"LaunchAgent installed and started!")
        print(f"  - Runs every 3 minutes")
        print(f"  - Repository: {repo_path}")
    else:
        print(f"Failed to load LaunchAgent: {result.stderr}")


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
        print("No LaunchAgent found")


def show_status():
    """Show current status."""
    repo_path = get_repo_root()
    runner = SelfAIRunner(repo_path)
    stats = runner.db.get_stats()

    print("\n=== SelfAI Status (Planning-First Workflow) ===")
    print(f"Repository: {repo_path}")

    print(f"\nTask Status:")
    for status, count in stats.items():
        if count > 0:
            print(f"  {status}: {count}")

    # Show plan_review tasks that need attention
    review_tasks = runner.db.get_plan_review_tasks()
    if review_tasks:
        print(f"\n⚠️  Plans Awaiting Review:")
        for task in review_tasks[:5]:
            print(f"  #{task['id']}: {task['title']}")
        if len(review_tasks) > 5:
            print(f"  ... and {len(review_tasks) - 5} more")
        print(f"\n  Use: python -m selfai approve <id>")
        print(f"  Or:  python -m selfai feedback <id> \"your feedback\"")

    # Show cancelled tasks
    cancelled = runner.db.get_cancelled_tasks()
    if cancelled:
        print(f"\n❌ Cancelled Tasks (need feedback):")
        for task in cancelled[:3]:
            print(f"  #{task['id']}: {task['title']}")
        print(f"\n  Use: python -m selfai reenable <id> [\"feedback\"]")


def open_dashboard():
    """Open the dashboard in browser via server."""
    import threading
    import time
    from http.server import HTTPServer
    from .server import create_handler

    repo_path = get_repo_root()

    # Update dashboard first
    runner = SelfAIRunner(repo_path)
    runner.update_dashboard()

    # Start server in background thread
    handler = create_handler(repo_path)
    server = HTTPServer(('localhost', 8787), handler)

    def serve():
        server.serve_forever()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    # Give server time to start
    time.sleep(0.5)

    # Open browser
    webbrowser.open('http://localhost:8787/')
    print(f"Dashboard opened at http://localhost:8787/")
    print("Press Ctrl+C to stop")

    try:
        # Keep main thread alive
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nServer stopped")
        server.shutdown()


def serve_dashboard(port: int = 8787):
    """Start the dashboard server."""
    repo_path = get_repo_root()
    print(f"Starting SelfAI Dashboard Server for: {repo_path}")
    run_server(host='localhost', port=port, repo_path=repo_path)


def run_once(discover: bool = False):
    """Run a single improvement cycle."""
    repo_path = get_repo_root()
    print(f"Running SelfAI for: {repo_path}")

    runner = SelfAIRunner(repo_path)
    runner.run(discover=discover)

    stats = runner.db.get_stats()
    print(f"\nStatus: {stats.get('completed', 0)} completed, {stats.get('in_progress', 0)} in progress")
    print(f"        {stats.get('plan_review', 0)} awaiting review, {stats.get('pending', 0)} pending")


def run_discovery(categories: list = None):
    """Run improvement discovery scan."""
    from .discovery import DiscoveryCategory

    repo_path = get_repo_root()
    runner = SelfAIRunner(repo_path)

    print("\n🔍 Discovering improvements...")
    if categories:
        print(f"Categories: {', '.join(categories)}")
    else:
        print("Categories: all")

    discovered = runner._discover_existing_features(categories)

    runner.update_dashboard()
    print(f"\n✅ Found {discovered} new improvements")
    print("Run 'python -m selfai status' to see pending tasks")


def add_improvement(title: str, description: str = ''):
    """Add a new improvement task."""
    repo_path = get_repo_root()
    runner = SelfAIRunner(repo_path)

    imp_id = runner.db.add(
        title=title,
        description=description,
        source='manual'
    )

    runner.update_dashboard()
    print(f"Added task #{imp_id}: {title}")
    print("  Status: pending (will be planned on next run)")


def approve_plan(task_id: int):
    """Approve a plan for execution."""
    repo_path = get_repo_root()
    runner = SelfAIRunner(repo_path)

    task = runner.db.get_by_id(task_id)
    if not task:
        print(f"Error: Task #{task_id} not found")
        return

    if task['status'] != 'plan_review':
        print(f"Error: Task #{task_id} is not awaiting review (status: {task['status']})")
        return

    runner.db.approve_plan(task_id)
    runner.update_dashboard()
    print(f"✅ Approved plan for #{task_id}: {task['title']}")
    print("  Will be executed on next run")


def provide_feedback(task_id: int, feedback: str):
    """Provide feedback on a plan."""
    repo_path = get_repo_root()
    runner = SelfAIRunner(repo_path)

    task = runner.db.get_by_id(task_id)
    if not task:
        print(f"Error: Task #{task_id} not found")
        return

    if task['status'] != 'plan_review':
        print(f"Error: Task #{task_id} is not awaiting review (status: {task['status']})")
        return

    runner.db.request_plan_feedback(task_id, feedback)
    runner.update_dashboard()
    print(f"📝 Feedback submitted for #{task_id}")
    print("  Plan will be revised on next run")


def reenable_task(task_id: int, feedback: str = ''):
    """Re-enable a cancelled task."""
    repo_path = get_repo_root()
    runner = SelfAIRunner(repo_path)

    task = runner.db.get_by_id(task_id)
    if not task:
        print(f"Error: Task #{task_id} not found")
        return

    if task['status'] != 'cancelled':
        print(f"Error: Task #{task_id} is not cancelled (status: {task['status']})")
        return

    runner.db.re_enable_cancelled(task_id, feedback)
    runner.update_dashboard()
    print(f"🔄 Re-enabled task #{task_id}: {task['title']}")
    print("  Will be re-planned on next run")


def show_plan(task_id: int):
    """Show the plan for a task."""
    repo_path = get_repo_root()
    runner = SelfAIRunner(repo_path)

    task = runner.db.get_by_id(task_id)
    if not task:
        print(f"Error: Task #{task_id} not found")
        return

    print(f"\n=== Plan for #{task_id}: {task['title']} ===")
    print(f"Status: {task['status']}")
    print(f"Test Count: {task.get('test_count', 0)}/3")

    plan = task.get('plan_content', '')
    if plan:
        print(f"\n{plan}")
    else:
        print("\nNo plan generated yet")

    if task.get('user_feedback'):
        print(f"\n--- User Feedback ---")
        print(task['user_feedback'])


def analyze_logs():
    """Analyze logs for errors and issues."""
    from .runner import LogAnalyzer, CLAUDE_CMD
    repo_path = get_repo_root()
    data_dir = repo_path / '.selfai_data'

    analyzer = LogAnalyzer(data_dir, CLAUDE_CMD)
    analysis = analyzer.analyze_logs()

    print(f"\n=== Log Analysis ===")
    print(f"Analyzed {analysis['log_lines']} log lines")
    print(f"Found {analysis['issues_found']} issues\n")

    if analysis['issues']:
        print("Issues:")
        print("-" * 70)
        for i, issue in enumerate(analysis['issues'][:10], 1):
            issue_type = issue['type'].upper()
            detail = issue['detail'][:60]
            print(f"  {i}. [{issue_type}] {detail}")
            if len(issue['detail']) > 60:
                print(f"     {'...'}")

        if len(analysis['issues']) > 10:
            print(f"\n... and {len(analysis['issues']) - 10} more issues")

        print("\nUse 'python -m selfai diagnose' to run diagnosis on these issues")
    else:
        print("No issues found in logs.")


def diagnose_issues():
    """Diagnose issues found in logs."""
    from .runner import LogAnalyzer, CLAUDE_CMD
    repo_path = get_repo_root()
    data_dir = repo_path / '.selfai_data'

    analyzer = LogAnalyzer(data_dir, CLAUDE_CMD)
    analysis = analyzer.analyze_logs()

    if not analysis['issues']:
        print("No issues found in logs.")
        return

    print(f"\n=== Diagnosing {len(analysis['issues'])} issues ===\n")

    for i, issue in enumerate(analysis['issues'][:3], 1):
        print(f"{i}. Diagnosing [{issue['type'].upper()}]: {issue['detail'][:50]}...")
        try:
            diagnosis = analyzer.diagnose_and_fix(issue, repo_path)
            print(f"   Diagnosis: {diagnosis.get('diagnosis', 'N/A')[:80]}")
            print(f"   Confidence: {diagnosis.get('confidence', 0):.2f}")
            if diagnosis.get('fix_description'):
                print(f"   Fix: {diagnosis.get('fix_description', '')[:80]}")
            print()
        except Exception as e:
            print(f"   Error: {e}\n")

    if len(analysis['issues']) > 3:
        print(f"Diagnosed first 3 issues. {len(analysis['issues']) - 3} remaining.")


def show_monitoring_stats():
    """Show monitoring and self-healing statistics."""
    import sqlite3
    repo_path = get_repo_root()
    data_dir = repo_path / '.selfai_data'
    healing_db_path = data_dir / 'healing.db'

    if not healing_db_path.exists():
        print("\nNo monitoring data available yet.")
        print("Run 'python -m selfai run' to start monitoring.")
        return

    kb = KnowledgeBase(healing_db_path)
    stats = kb.get_statistics()

    print("\n=== Self-Healing Monitoring Statistics ===")
    print(f"Repository: {repo_path}\n")

    if not stats:
        print("No healing attempts recorded yet.")
        return

    total_attempts = sum(s['total_attempts'] for s in stats.values())
    total_successful = sum(s['successful'] for s in stats.values())
    overall_rate = (total_successful / total_attempts * 100) if total_attempts > 0 else 0

    print(f"Overall: {total_attempts} attempts, {total_successful} successful ({overall_rate:.1f}%)\n")
    print("By Error Type:")
    print("-" * 70)
    print(f"{'Error Type':<25} {'Attempts':<12} {'Success':<12} {'Rate':<10}")
    print("-" * 70)

    for error_type, stat in sorted(stats.items(), key=lambda x: x[1]['total_attempts'], reverse=True):
        success_rate = stat['success_rate'] * 100
        print(f"{error_type:<25} {stat['total_attempts']:<12} {stat['successful']:<12} {success_rate:.1f}%")

    print("\nRecent Healing History:")
    print("-" * 70)

    # Get recent history
    try:
        with sqlite3.connect(str(healing_db_path), timeout=30.0) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('''
                SELECT error_type, success, timestamp, diagnosis
                FROM healing_history
                ORDER BY timestamp DESC LIMIT 10
            ''')
            recent = [dict(row) for row in cursor.fetchall()]

        if recent:
            for record in recent:
                status = "✅" if record['success'] else "❌"
                timestamp = record['timestamp'][:19]  # Remove microseconds
                print(f"{status} {timestamp} - {record['error_type']}")
                if not record['success']:
                    print(f"   Diagnosis: {record['diagnosis'][:60]}...")
        else:
            print("No recent healing attempts.")
    except Exception as e:
        print(f"Error reading history: {e}")

    print("\n" + "=" * 70)


def print_help():
    """Print usage help."""
    print("""
SelfAI - Planning-First Autonomous Improvement System

Usage:
    python -m selfai <command> [options]

Commands:
    run [--discover] Run a single improvement cycle (optionally with discovery)
    discover [cats]  Discover improvements (categories: security, test_coverage,
                     refactoring, documentation, performance, code_quality)
    status           Show current status with tasks awaiting review
    monitor          Show self-healing monitoring statistics
    analyze-logs     Analyze system logs for errors and patterns
    diagnose         Diagnose issues found in logs using AI
    dashboard        Open dashboard in browser (starts server)
    serve [port]     Start dashboard server only (default port: 8787)
    add "title"      Add a new improvement task
    approve <id>     Approve a plan for execution
    feedback <id> "msg"  Provide feedback to revise a plan
    reenable <id>    Re-enable a cancelled task
    plan <id>        View the full plan for a task
    install          Install LaunchAgent (runs every 3 minutes)
    uninstall        Remove LaunchAgent
    help             Show this help

Workflow:
    1. Tasks start as 'pending'
    2. Plans are generated with internet research
    3. Plans go to 'plan_review' for your approval
    4. Approved tasks are executed
    5. Features are tested (max 3 attempts)
    6. After 3 failures -> cancelled (needs feedback to re-enable)

Examples:
    python -m selfai run                     # Run once manually
    python -m selfai run --discover          # Run with discovery phase
    python -m selfai discover                # Discover all improvements
    python -m selfai discover security       # Discover only security issues
    python -m selfai status                  # Check status
    python -m selfai add "Add dark mode"     # Add new task
    python -m selfai approve 5               # Approve plan #5
    python -m selfai feedback 5 "Use CSS variables"  # Request changes
    python -m selfai reenable 3              # Re-enable cancelled task
    python -m selfai plan 5                  # View plan for task #5
    python -m selfai dashboard               # Open dashboard
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
        # Check for --discover flag
        discover = '--discover' in sys.argv
        run_once(discover=discover)
    elif command == 'discover':
        # Get optional categories from remaining args
        categories = sys.argv[2:] if len(sys.argv) > 2 else None
        if categories:
            # Validate categories
            valid_cats = ['security', 'test_coverage', 'refactoring', 'documentation', 'performance', 'code_quality']
            invalid = [c for c in categories if c not in valid_cats]
            if invalid:
                print(f"Error: Invalid categories: {', '.join(invalid)}")
                print(f"Valid categories: {', '.join(valid_cats)}")
                return
        run_discovery(categories)
    elif command == 'status':
        show_status()
    elif command == 'dashboard':
        open_dashboard()
    elif command == 'serve':
        port = 8787
        if len(sys.argv) > 2:
            try:
                port = int(sys.argv[2])
            except ValueError:
                print("Error: port must be a number")
                return
        serve_dashboard(port)
    elif command == 'add':
        if len(sys.argv) < 3:
            print("Usage: python -m selfai add \"title\" [description]")
            return
        title = sys.argv[2]
        description = sys.argv[3] if len(sys.argv) > 3 else ''
        add_improvement(title, description)
    elif command == 'approve':
        if len(sys.argv) < 3:
            print("Usage: python -m selfai approve <task_id>")
            return
        try:
            task_id = int(sys.argv[2])
            approve_plan(task_id)
        except ValueError:
            print("Error: task_id must be a number")
    elif command == 'feedback':
        if len(sys.argv) < 4:
            print('Usage: python -m selfai feedback <task_id> "feedback message"')
            return
        try:
            task_id = int(sys.argv[2])
            feedback = sys.argv[3]
            provide_feedback(task_id, feedback)
        except ValueError:
            print("Error: task_id must be a number")
    elif command == 'reenable':
        if len(sys.argv) < 3:
            print('Usage: python -m selfai reenable <task_id> ["optional feedback"]')
            return
        try:
            task_id = int(sys.argv[2])
            feedback = sys.argv[3] if len(sys.argv) > 3 else ''
            reenable_task(task_id, feedback)
        except ValueError:
            print("Error: task_id must be a number")
    elif command == 'plan':
        if len(sys.argv) < 3:
            print("Usage: python -m selfai plan <task_id>")
            return
        try:
            task_id = int(sys.argv[2])
            show_plan(task_id)
        except ValueError:
            print("Error: task_id must be a number")
    elif command == 'monitor':
        show_monitoring_stats()
    elif command == 'analyze-logs':
        analyze_logs()
    elif command == 'diagnose':
        diagnose_issues()
    elif command in ('help', '-h', '--help'):
        print_help()
    else:
        print(f"Unknown command: {command}")
        print_help()


if __name__ == '__main__':
    main()
