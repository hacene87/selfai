#!/usr/bin/env python3
"""Demo script showing log analysis feature usage.

This demonstrates how to use the LogAnalyzer to:
1. Analyze logs for errors and issues
2. Generate sample log data
3. View analysis results
"""
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from selfai.runner import LogAnalyzer


def create_sample_logs(log_file: Path):
    """Create sample log file with various types of messages."""
    sample_log = """2025-01-01 10:00:00 - INFO - selfai - System started
2025-01-01 10:00:05 - INFO - selfai - Processing feature #1
2025-01-01 10:00:10 - INFO - selfai - Feature completed successfully
2025-01-01 10:00:15 - WARNING - selfai - Slow response time detected
2025-01-01 10:00:20 - ERROR: Database connection failed - retrying
2025-01-01 10:00:25 - INFO - selfai - Database connection restored
2025-01-01 10:00:30 - INFO - selfai - Processing feature #2
2025-01-01 10:00:35 - Exception: FileNotFoundError in module loader
2025-01-01 10:00:40 - INFO - selfai - Retrying operation
2025-01-01 10:00:45 - Failed to parse JSON response
2025-01-01 10:00:50 - INFO - selfai - Using fallback parser
2025-01-01 10:00:55 - Timeout waiting for Claude response
2025-01-01 10:01:00 - INFO - selfai - Operation completed with warnings
2025-01-01 10:01:05 - CONFLICT: Git merge conflict detected
2025-01-01 10:01:10 - INFO - selfai - Attempting conflict resolution
2025-01-01 10:01:15 - INFO - selfai - All operations completed
"""
    log_file.write_text(sample_log)
    print(f"Created sample log file: {log_file}")


def demo_basic_analysis(analyzer: LogAnalyzer):
    """Demonstrate basic log analysis."""
    print("\n=== Basic Log Analysis ===")

    # Run analysis
    analysis = analyzer.analyze_logs()

    print(f"Log lines analyzed: {analysis['log_lines']}")
    print(f"Issues found: {analysis['issues_found']}")

    # Display issues
    if analysis['issues']:
        print("\nDetected Issues:")
        for i, issue in enumerate(analysis['issues'], 1):
            print(f"  {i}. Type: {issue['type'].upper()}")
            print(f"     Detail: {issue['detail']}")
            print(f"     Time: {issue['timestamp'][:19]}")
            print()


def demo_get_logs(analyzer: LogAnalyzer):
    """Demonstrate retrieving recent logs."""
    print("\n=== Retrieving Recent Logs ===")

    # Get last 5 lines
    recent = analyzer.get_recent_logs(lines=5)
    print("Last 5 log lines:")
    for line in recent.split('\n'):
        if line.strip():
            print(f"  {line}")


def demo_save_and_load(analyzer: LogAnalyzer):
    """Demonstrate saving issues for later review."""
    print("\n=== Saving Issues ===")

    # Run analysis
    analysis = analyzer.analyze_logs()

    # Save issues
    if analysis['issues']:
        analyzer.save_issues(analysis['issues'])
        print(f"Saved {len(analysis['issues'])} issues to {analyzer.issues_file}")

        # Check file exists
        if analyzer.issues_file.exists():
            print(f"Issues file size: {analyzer.issues_file.stat().st_size} bytes")


def main():
    """Run the demo."""
    print("=== SelfAI Log Analysis Demo ===")

    # Setup
    demo_path = Path(__file__).parent / 'demo_logs'
    demo_path.mkdir(exist_ok=True)

    log_file = demo_path / 'runner.log'

    # Create sample logs
    create_sample_logs(log_file)

    # Initialize analyzer
    analyzer = LogAnalyzer(demo_path, 'claude')

    # Run demos
    demo_basic_analysis(analyzer)
    demo_get_logs(analyzer)
    demo_save_and_load(analyzer)

    print("\n=== Demo Complete ===")
    print(f"\nDemo files created in: {demo_path}")
    print("You can inspect:")
    print(f"  - Log file: {log_file}")
    print(f"  - Issues JSON: {analyzer.issues_file}")

    # Cleanup option
    print("\nTo clean up demo files, delete:", demo_path)


if __name__ == '__main__':
    main()
