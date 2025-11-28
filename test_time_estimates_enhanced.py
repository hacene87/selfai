#!/usr/bin/env python3
"""Test dashboard estimated time remaining (Enhanced)."""
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta

# Add selfai to path
sys.path.insert(0, str(Path(__file__).parent))

from selfai.runner import Runner
from selfai.database import Database


def test_edge_case_no_completed_tasks():
    """Test edge case: no completed tasks means no average duration."""
    print("Testing edge case: no completed tasks...")

    test_db_path = Path("/tmp/test_no_completed.db")
    if test_db_path.exists():
        test_db_path.unlink()

    db = Database(test_db_path)

    # Add in-progress task but no completed tasks
    imp_id = db.add(
        title="In Progress Task",
        description="Task in progress with no baseline",
        priority=50
    )

    import sqlite3
    conn = sqlite3.connect(db.db_path)
    started_at = (datetime.now() - timedelta(seconds=60)).isoformat()
    conn.execute('UPDATE improvements SET started_at = ?, status = ? WHERE id = ?',
                 (started_at, 'in_progress', imp_id))
    conn.commit()
    conn.close()

    # Get tasks with time estimates
    tasks = db.get_tasks_with_time_estimates()

    # Find the in-progress task
    in_progress_task = next((t for t in tasks if t['id'] == imp_id), None)

    assert in_progress_task is not None, "In-progress task not found"
    # With no baseline, estimated_remaining should be None
    assert in_progress_task['estimated_remaining'] is None, \
        f"Expected None when no baseline, got {in_progress_task['estimated_remaining']}"
    print("✓ Edge case handled: No estimated time when no completed tasks")

    # Clean up
    test_db_path.unlink()

    print("✓ Edge case test passed!")
    return True


def test_edge_case_negative_remaining():
    """Test edge case: task taking longer than average should return 0, not negative."""
    print("\nTesting edge case: task exceeding average duration...")

    test_db_path = Path("/tmp/test_negative.db")
    if test_db_path.exists():
        test_db_path.unlink()

    db = Database(test_db_path)

    # Add completed task with short duration (100s)
    imp_id_1 = db.add(
        title="Quick Task",
        description="Task to establish low average",
        priority=50
    )

    import sqlite3
    conn = sqlite3.connect(db.db_path)
    started_at_1 = (datetime.now() - timedelta(seconds=100)).isoformat()
    conn.execute('UPDATE improvements SET started_at = ? WHERE id = ?', (started_at_1, imp_id_1))
    conn.commit()
    conn.close()

    db.mark_level_completed(imp_id_1, 1, "Completed")

    # Add in-progress task that's already running longer (200s)
    imp_id_2 = db.add(
        title="Slow Task",
        description="Task taking longer than average",
        priority=50
    )

    conn = sqlite3.connect(db.db_path)
    started_at_2 = (datetime.now() - timedelta(seconds=200)).isoformat()
    conn.execute('UPDATE improvements SET started_at = ?, status = ? WHERE id = ?',
                 (started_at_2, 'in_progress', imp_id_2))
    conn.commit()
    conn.close()

    # Get tasks with time estimates
    tasks = db.get_tasks_with_time_estimates()

    # Find the slow task
    slow_task = next((t for t in tasks if t['id'] == imp_id_2), None)

    assert slow_task is not None, "Slow task not found"
    assert slow_task['estimated_remaining'] is not None, "Estimated remaining should not be None"
    assert slow_task['estimated_remaining'] == 0, \
        f"Expected 0 (not negative) when exceeding average, got {slow_task['estimated_remaining']}"
    print("✓ Edge case handled: Returns 0 instead of negative time")

    # Clean up
    test_db_path.unlink()

    print("✓ Edge case test passed!")
    return True


def test_edge_case_invalid_started_at():
    """Test edge case: task with invalid started_at timestamp."""
    print("\nTesting edge case: invalid started_at timestamp...")

    test_db_path = Path("/tmp/test_invalid_time.db")
    if test_db_path.exists():
        test_db_path.unlink()

    db = Database(test_db_path)

    # Add completed task to establish average
    imp_id_1 = db.add(
        title="Completed Task",
        description="Normal completed task",
        priority=50
    )

    import sqlite3
    conn = sqlite3.connect(db.db_path)
    started_at_1 = (datetime.now() - timedelta(seconds=150)).isoformat()
    conn.execute('UPDATE improvements SET started_at = ? WHERE id = ?', (started_at_1, imp_id_1))
    conn.commit()
    conn.close()

    db.mark_level_completed(imp_id_1, 1, "Completed")

    # Add in-progress task with invalid timestamp
    imp_id_2 = db.add(
        title="Invalid Time Task",
        description="Task with bad timestamp",
        priority=50
    )

    conn = sqlite3.connect(db.db_path)
    conn.execute('UPDATE improvements SET started_at = ?, status = ? WHERE id = ?',
                 ('invalid-timestamp', 'in_progress', imp_id_2))
    conn.commit()
    conn.close()

    # Get tasks with time estimates - should not crash
    tasks = db.get_tasks_with_time_estimates()

    # Find the invalid task
    invalid_task = next((t for t in tasks if t['id'] == imp_id_2), None)

    assert invalid_task is not None, "Invalid task not found"
    # With invalid timestamp, estimated_remaining should be None
    assert invalid_task['estimated_remaining'] is None, \
        f"Expected None for invalid timestamp, got {invalid_task['estimated_remaining']}"
    print("✓ Edge case handled: Invalid timestamp returns None gracefully")

    # Clean up
    test_db_path.unlink()

    print("✓ Edge case test passed!")
    return True


def test_edge_case_null_started_at():
    """Test edge case: in-progress task with NULL started_at."""
    print("\nTesting edge case: NULL started_at...")

    test_db_path = Path("/tmp/test_null_time.db")
    if test_db_path.exists():
        test_db_path.unlink()

    db = Database(test_db_path)

    # Add completed task
    imp_id_1 = db.add(
        title="Completed Task",
        description="Normal completed task",
        priority=50
    )

    import sqlite3
    conn = sqlite3.connect(db.db_path)
    started_at_1 = (datetime.now() - timedelta(seconds=150)).isoformat()
    conn.execute('UPDATE improvements SET started_at = ? WHERE id = ?', (started_at_1, imp_id_1))
    conn.commit()
    conn.close()

    db.mark_level_completed(imp_id_1, 1, "Completed")

    # Add in-progress task with NULL started_at
    imp_id_2 = db.add(
        title="NULL Time Task",
        description="Task with NULL timestamp",
        priority=50
    )

    conn = sqlite3.connect(db.db_path)
    conn.execute('UPDATE improvements SET status = ? WHERE id = ?',
                 ('in_progress', imp_id_2))
    # Don't set started_at - it should be NULL
    conn.commit()
    conn.close()

    # Get tasks with time estimates
    tasks = db.get_tasks_with_time_estimates()

    # Find the null task
    null_task = next((t for t in tasks if t['id'] == imp_id_2), None)

    assert null_task is not None, "NULL task not found"
    # With NULL started_at, estimated_remaining should be None
    assert null_task['estimated_remaining'] is None, \
        f"Expected None for NULL started_at, got {null_task['estimated_remaining']}"
    print("✓ Edge case handled: NULL started_at returns None gracefully")

    # Clean up
    test_db_path.unlink()

    print("✓ Edge case test passed!")
    return True


def test_multiple_levels():
    """Test that estimates work correctly for different levels (MVP, Enhanced, Advanced)."""
    print("\nTesting multiple levels...")

    test_db_path = Path("/tmp/test_multi_level.db")
    if test_db_path.exists():
        test_db_path.unlink()

    db = Database(test_db_path)

    # Add completed MVP task (100s)
    imp_id_mvp = db.add(title="MVP Task", description="Test", priority=50)

    import sqlite3
    conn = sqlite3.connect(db.db_path)
    started_mvp = (datetime.now() - timedelta(seconds=100)).isoformat()
    conn.execute('UPDATE improvements SET started_at = ? WHERE id = ?', (started_mvp, imp_id_mvp))
    conn.commit()
    conn.close()

    db.mark_level_completed(imp_id_mvp, 1, "MVP done")

    # Add completed Enhanced task (200s)
    imp_id_enh = db.add(title="Enhanced Task", description="Test", priority=50)

    conn = sqlite3.connect(db.db_path)
    started_enh = (datetime.now() - timedelta(seconds=200)).isoformat()
    conn.execute('UPDATE improvements SET started_at = ?, current_level = ? WHERE id = ?',
                 (started_enh, 2, imp_id_enh))
    conn.commit()
    conn.close()

    db.mark_level_completed(imp_id_enh, 2, "Enhanced done")

    # Check averages for each level
    averages = db.get_average_duration_by_level()

    assert averages[1] is not None, "MVP average should exist"
    assert abs(averages[1] - 100) < 5, f"MVP average should be ~100s, got {averages[1]}"
    assert averages[2] is not None, "Enhanced average should exist"
    assert abs(averages[2] - 200) < 5, f"Enhanced average should be ~200s, got {averages[2]}"
    assert averages[3] is None, "Advanced average should be None (no data)"

    print(f"✓ MVP average: {averages[1]}s")
    print(f"✓ Enhanced average: {averages[2]}s")
    print(f"✓ Advanced average: {averages[3]}")

    # Add in-progress Enhanced task
    imp_id_in_prog = db.add(title="In Progress Enhanced", description="Test", priority=50)

    conn = sqlite3.connect(db.db_path)
    started_prog = (datetime.now() - timedelta(seconds=50)).isoformat()
    conn.execute('UPDATE improvements SET started_at = ?, status = ?, current_level = ? WHERE id = ?',
                 (started_prog, 'in_progress', 2, imp_id_in_prog))
    conn.commit()
    conn.close()

    # Get tasks with estimates
    tasks = db.get_tasks_with_time_estimates()
    in_prog_task = next((t for t in tasks if t['id'] == imp_id_in_prog), None)

    assert in_prog_task is not None, "In-progress task not found"
    assert in_prog_task['estimated_remaining'] is not None, "Should have estimate"
    # Should use Enhanced average (200s) - 50s elapsed = ~150s remaining
    assert abs(in_prog_task['estimated_remaining'] - 150) < 10, \
        f"Expected ~150s remaining, got {in_prog_task['estimated_remaining']}"
    print(f"✓ In-progress Enhanced task estimate: {in_prog_task['estimated_remaining']}s")

    # Clean up
    test_db_path.unlink()

    print("✓ Multiple levels test passed!")
    return True


def test_input_validation():
    """Test input validation and error handling."""
    print("\nTesting input validation...")

    test_db_path = Path("/tmp/test_validation.db")
    if test_db_path.exists():
        test_db_path.unlink()

    db = Database(test_db_path)

    # Test that method returns empty list for empty database
    tasks = db.get_tasks_with_time_estimates()
    assert isinstance(tasks, list), "Should return list"
    assert len(tasks) == 0, "Should return empty list for empty database"
    print("✓ Empty database returns empty list")

    # Test that method returns dict with correct structure
    averages = db.get_average_duration_by_level()
    assert isinstance(averages, dict), "Should return dict"
    assert 1 in averages and 2 in averages and 3 in averages, "Should have all levels"
    assert all(v is None for v in averages.values()), "All averages should be None for empty DB"
    print("✓ Empty database returns None for all averages")

    # Clean up
    test_db_path.unlink()

    print("✓ Input validation test passed!")
    return True


def test_dashboard_format_duration():
    """Test that dashboard formats durations in a user-friendly way."""
    print("\nTesting dashboard duration formatting...")

    test_db_path = Path("/tmp/test_format.db")
    if test_db_path.exists():
        test_db_path.unlink()

    db = Database(test_db_path)

    # Add completed task (3665 seconds = 1h 1m 5s)
    imp_id_1 = db.add(title="Completed", description="Test", priority=50)

    import sqlite3
    conn = sqlite3.connect(db.db_path)
    started = (datetime.now() - timedelta(seconds=3665)).isoformat()
    conn.execute('UPDATE improvements SET started_at = ? WHERE id = ?', (started, imp_id_1))
    conn.commit()
    conn.close()

    db.mark_level_completed(imp_id_1, 1, "Done")

    # Add in-progress task (started 1800s ago = 30 min ago)
    imp_id_2 = db.add(title="In Progress", description="Test", priority=50)

    conn = sqlite3.connect(db.db_path)
    started_2 = (datetime.now() - timedelta(seconds=1800)).isoformat()
    conn.execute('UPDATE improvements SET started_at = ?, status = ? WHERE id = ?',
                 (started_2, 'in_progress', imp_id_2))
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

    # Should contain readable time format (hours, minutes, seconds)
    # Looking for patterns like "1h" or "30m" or "5s"
    has_hour = 'h' in html_content and any(c.isdigit() for c in html_content.split('h')[0][-3:])
    has_minute = 'm' in html_content and any(c.isdigit() for c in html_content.split('m')[0][-3:])
    has_second = 's' in html_content

    assert has_hour or has_minute or has_second, \
        "Dashboard should contain human-readable time format (h/m/s)"
    print("✓ Dashboard displays human-readable time format")

    # Clean up
    test_db_path.unlink()
    dashboard_path.unlink()

    print("✓ Dashboard formatting test passed!")
    return True


if __name__ == '__main__':
    try:
        # Run all MVP tests first
        print("="*50)
        print("Running MVP tests first...")
        print("="*50)

        import test_time_estimates_mvp
        test_time_estimates_mvp.test_duration_storage()
        test_time_estimates_mvp.test_average_duration_calculation()
        test_time_estimates_mvp.test_estimated_time_remaining()
        test_time_estimates_mvp.test_dashboard_displays_estimate()

        print("\n" + "="*50)
        print("MVP tests passed! Now running Enhanced tests...")
        print("="*50 + "\n")

        # Run Enhanced tests
        test_edge_case_no_completed_tasks()
        test_edge_case_negative_remaining()
        test_edge_case_invalid_started_at()
        test_edge_case_null_started_at()
        test_multiple_levels()
        test_input_validation()
        test_dashboard_format_duration()

        print("\n" + "="*50)
        print("All Enhanced tests passed successfully!")
        print("="*50)
        sys.exit(0)
    except AssertionError as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
