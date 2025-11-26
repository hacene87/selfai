#!/usr/bin/env python3
"""Test dashboard current level progress indicator (MVP)."""
import sys
from pathlib import Path

# Add selfai to path
sys.path.insert(0, str(Path(__file__).parent))

from selfai.runner import Runner
from selfai.database import Database


def test_level_progress_indicator():
    """Test that the progress indicator shows correct workflow stages."""
    print("Testing level progress indicator...")

    # Create a test database
    test_db_path = Path("/tmp/test_dashboard_progress.db")
    if test_db_path.exists():
        test_db_path.unlink()

    db = Database(test_db_path)

    # Add a test improvement with different progress states
    imp_id = db.add(
        title="Test Feature",
        description="Test feature for progress indicator",
        priority=50
    )

    # Test Case 1: No plan yet (all pending)
    imp = db.get_by_id(imp_id)
    runner = Runner(Path("/tmp"))
    progress = runner._get_level_progress_indicator(imp, 1)
    assert '○ → ○ → ○' in progress, f"Expected '○ → ○ → ○' but got '{progress}'"
    print("✓ Test 1 passed: No plan (○ → ○ → ○)")

    # Test Case 2: Plan added
    db.save_plan(imp_id, 1, "Test plan")
    imp = db.get_by_id(imp_id)
    progress = runner._get_level_progress_indicator(imp, 1)
    assert '● → ○ → ○' in progress, f"Expected '● → ○ → ○' but got '{progress}'"
    print("✓ Test 2 passed: Plan added (● → ○ → ○)")

    # Test Case 3: Output added
    db.mark_level_completed(imp_id, 1, "Test output")
    imp = db.get_by_id(imp_id)
    progress = runner._get_level_progress_indicator(imp, 1)
    assert '● → ● → ○' in progress, f"Expected '● → ● → ○' but got '{progress}'"
    print("✓ Test 3 passed: Output added (● → ● → ○)")

    # Test Case 4: Test passed
    db.mark_test_passed(imp_id, 1, "Test passed")
    imp = db.get_by_id(imp_id)
    progress = runner._get_level_progress_indicator(imp, 1)
    assert '● → ● → ✓' in progress, f"Expected '● → ● → ✓' but got '{progress}'"
    print("✓ Test 4 passed: Test passed (● → ● → ✓)")

    # Test Case 5: Test failed
    db.mark_test_failed(imp_id, 1, "Test failed")
    imp = db.get_by_id(imp_id)
    progress = runner._get_level_progress_indicator(imp, 1)
    assert '● → ● → ✗' in progress, f"Expected '● → ● → ✗' but got '{progress}'"
    print("✓ Test 5 passed: Test failed (● → ● → ✗)")

    # Clean up
    test_db_path.unlink()

    print("\n✓ All tests passed!")
    return True


def test_dashboard_html_generation():
    """Test that the dashboard HTML includes the progress indicator."""
    print("\nTesting dashboard HTML generation...")

    # Create a test database
    test_db_path = Path("/tmp/test_dashboard_html.db")
    if test_db_path.exists():
        test_db_path.unlink()

    db = Database(test_db_path)

    # Add a test improvement
    imp_id = db.add(
        title="Test Feature for Dashboard",
        description="Test feature",
        priority=50
    )
    db.save_plan(imp_id, 1, "Test plan")
    db.mark_level_completed(imp_id, 1, "Test output")

    # Generate dashboard
    runner = Runner(Path("/tmp"))
    runner.db = db
    runner.workspace_path = Path("/tmp")
    runner.update_dashboard()

    # Check dashboard HTML
    dashboard_path = Path("/tmp/dashboard.html")
    assert dashboard_path.exists(), "Dashboard HTML not created"

    html_content = dashboard_path.read_text()

    # Check for CSS class
    assert '.level-progress' in html_content, "CSS class not found in dashboard"
    print("✓ CSS class found in dashboard")

    # Check for progress indicator in HTML
    assert 'level-progress' in html_content, "Progress indicator not found in dashboard HTML"
    print("✓ Progress indicator found in dashboard HTML")

    # Check for arrows in progress
    assert '→' in html_content, "Arrow separator not found in progress indicator"
    print("✓ Arrow separator found in progress indicator")

    # Clean up
    test_db_path.unlink()
    dashboard_path.unlink()

    print("\n✓ Dashboard HTML generation test passed!")
    return True


if __name__ == '__main__':
    try:
        test_level_progress_indicator()
        test_dashboard_html_generation()
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
