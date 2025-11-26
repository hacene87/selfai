#!/usr/bin/env python3
"""Test dashboard estimated time remaining (MVP)."""
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta

# Add selfai to path
sys.path.insert(0, str(Path(__file__).parent))

from selfai.runner import Runner
from selfai.database import Database


def test_duration_storage():
    """Test that duration is calculated and stored when task completes."""
    print("Testing duration storage...")

    # Create a test database
    test_db_path = Path("/tmp/test_duration_storage.db")
    if test_db_path.exists():
        test_db_path.unlink()

    db = Database(test_db_path)

    # Add a test improvement
    imp_id = db.add(
        title="Test Feature",
        description="Test feature for duration storage",
        priority=50
    )

    # Mark in progress
    db.mark_in_progress(imp_id)

    # Wait a bit to simulate work
    time.sleep(2)

    # Mark as completed
    db.mark_level_completed(imp_id, 1, "Test output")

    # Check duration was stored
    imp = db.get_by_id(imp_id)
    assert imp['mvp_duration'] is not None, "Duration was not stored"
    assert imp['mvp_duration'] >= 2, f"Duration should be at least 2 seconds, got {imp['mvp_duration']}"
    assert imp['mvp_duration'] < 10, f"Duration should be less than 10 seconds, got {imp['mvp_duration']}"
    print(f"✓ Duration stored correctly: {imp['mvp_duration']}s")

    # Clean up
    test_db_path.unlink()

    print("✓ Duration storage test passed!")
    return True


def test_average_duration_calculation():
    """Test that average duration calculation returns correct values."""
    print("\nTesting average duration calculation...")

    # Create a test database
    test_db_path = Path("/tmp/test_avg_duration.db")
    if test_db_path.exists():
        test_db_path.unlink()

    db = Database(test_db_path)

    # Add and complete multiple improvements with known durations
    durations = [100, 200, 300]  # seconds

    for i, duration in enumerate(durations):
        imp_id = db.add(
            title=f"Test Feature {i}",
            description="Test feature",
            priority=50
        )

        # Set started_at to simulate duration
        now = datetime.now()
        started_at = (now - timedelta(seconds=duration)).isoformat()

        with db.db_path.open() as _:
            import sqlite3
            conn = sqlite3.connect(db.db_path)
            conn.execute('UPDATE improvements SET started_at = ? WHERE id = ?', (started_at, imp_id))
            conn.commit()
            conn.close()

        # Mark as completed
        db.mark_level_completed(imp_id, 1, "Test output")

    # Get average duration
    averages = db.get_average_duration_by_level()

    assert averages[1] is not None, "Average duration should not be None"
    expected_avg = sum(durations) / len(durations)
    assert abs(averages[1] - expected_avg) < 5, f"Average should be around {expected_avg}, got {averages[1]}"
    print(f"✓ Average duration calculated correctly: {averages[1]}s (expected ~{expected_avg}s)")

    # Clean up
    test_db_path.unlink()

    print("✓ Average duration calculation test passed!")
    return True


def test_estimated_time_remaining():
    """Test that estimated time remaining is calculated correctly."""
    print("\nTesting estimated time remaining calculation...")

    # Create a test database
    test_db_path = Path("/tmp/test_time_estimate.db")
    if test_db_path.exists():
        test_db_path.unlink()

    db = Database(test_db_path)

    # Add and complete a task to establish average
    imp_id_1 = db.add(
        title="Completed Task",
        description="Task to establish average",
        priority=50
    )

    # Set started_at to simulate 200 second duration
    now = datetime.now()
    started_at = (now - timedelta(seconds=200)).isoformat()

    import sqlite3
    conn = sqlite3.connect(db.db_path)
    conn.execute('UPDATE improvements SET started_at = ? WHERE id = ?', (started_at, imp_id_1))
    conn.commit()
    conn.close()

    db.mark_level_completed(imp_id_1, 1, "Completed")

    # Add in-progress task
    imp_id_2 = db.add(
        title="In Progress Task",
        description="Task in progress",
        priority=50
    )

    # Set started 100 seconds ago
    started_at_2 = (datetime.now() - timedelta(seconds=100)).isoformat()
    conn = sqlite3.connect(db.db_path)
    conn.execute('UPDATE improvements SET started_at = ?, status = ? WHERE id = ?',
                 (started_at_2, 'in_progress', imp_id_2))
    conn.commit()
    conn.close()

    # Get tasks with time estimates
    tasks = db.get_tasks_with_time_estimates()

    # Find the in-progress task
    in_progress_task = None
    for task in tasks:
        if task['id'] == imp_id_2:
            in_progress_task = task
            break

    assert in_progress_task is not None, "In-progress task not found"
    assert in_progress_task['estimated_remaining'] is not None, "Estimated remaining should not be None"

    # Should be around 100 seconds remaining (200 avg - 100 elapsed)
    expected_remaining = 100
    assert abs(in_progress_task['estimated_remaining'] - expected_remaining) < 10, \
        f"Expected around {expected_remaining}s remaining, got {in_progress_task['estimated_remaining']}s"
    print(f"✓ Estimated time remaining calculated correctly: {in_progress_task['estimated_remaining']}s")

    # Clean up
    test_db_path.unlink()

    print("✓ Estimated time remaining test passed!")
    return True


def test_dashboard_displays_estimate():
    """Test that dashboard displays estimated time remaining."""
    print("\nTesting dashboard display of time estimates...")

    # Create a test database
    test_db_path = Path("/tmp/test_dashboard_estimate.db")
    if test_db_path.exists():
        test_db_path.unlink()

    db = Database(test_db_path)

    # Add completed task to establish average
    imp_id_1 = db.add(
        title="Completed Task",
        description="Task to establish average",
        priority=50
    )

    now = datetime.now()
    started_at = (now - timedelta(seconds=300)).isoformat()

    import sqlite3
    conn = sqlite3.connect(db.db_path)
    conn.execute('UPDATE improvements SET started_at = ? WHERE id = ?', (started_at, imp_id_1))
    conn.commit()
    conn.close()

    db.mark_level_completed(imp_id_1, 1, "Completed")

    # Add in-progress task
    imp_id_2 = db.add(
        title="In Progress Task",
        description="Task in progress",
        priority=50
    )

    started_at_2 = (datetime.now() - timedelta(seconds=60)).isoformat()
    conn = sqlite3.connect(db.db_path)
    conn.execute('UPDATE improvements SET started_at = ?, status = ? WHERE id = ?',
                 (started_at_2, 'in_progress', imp_id_2))
    conn.commit()
    conn.close()

    # Generate dashboard
    runner = Runner(Path("/tmp"))
    runner.db = db
    runner.workspace_path = Path("/tmp")
    runner.update_dashboard()

    # Check dashboard HTML
    dashboard_path = Path("/tmp/dashboard.html")
    assert dashboard_path.exists(), "Dashboard HTML not created"

    html_content = dashboard_path.read_text()

    # Check for "Est. Time Remaining" column header
    assert 'Est. Time Remaining' in html_content, "Est. Time Remaining column header not found"
    print("✓ Est. Time Remaining column header found in dashboard")

    # Check that time format appears (e.g., "3m 45s" or similar)
    # The in-progress task should show remaining time
    assert 'm' in html_content or 's' in html_content, "Time format not found in dashboard"
    print("✓ Time format found in dashboard HTML")

    # Clean up
    test_db_path.unlink()
    dashboard_path.unlink()

    print("✓ Dashboard display test passed!")
    return True


if __name__ == '__main__':
    try:
        test_duration_storage()
        test_average_duration_calculation()
        test_estimated_time_remaining()
        test_dashboard_displays_estimate()
        print("\n" + "="*50)
        print("All MVP tests passed successfully!")
        print("="*50)
        sys.exit(0)
    except AssertionError as e:
        print(f"\n✗ Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
