#!/usr/bin/env python3
"""Test dashboard current level progress indicator (Enhanced)."""
import sys
from pathlib import Path

# Add selfai to path
sys.path.insert(0, str(Path(__file__).parent))

from selfai.runner import Runner
from selfai.database import Database


def test_edge_cases():
    """Test edge cases and error handling."""
    print("Testing edge cases...")

    test_db_path = Path("/tmp/test_dashboard_progress_enhanced.db")
    if test_db_path.exists():
        test_db_path.unlink()

    db = Database(test_db_path)
    runner = Runner(Path("/tmp"))
    runner.db = db

    # Test Case 1: None improvement
    progress = runner._get_level_progress_indicator(None, 1)
    assert '○ → ○ → ○' in progress, f"Expected fallback for None but got '{progress}'"
    print("✓ Test 1 passed: None improvement handled")

    # Test Case 2: Invalid level (too high)
    imp_id = db.add(title="Test", description="Test", priority=50)
    imp = db.get_by_id(imp_id)
    progress = runner._get_level_progress_indicator(imp, 99)
    assert '○ → ○ → ○' in progress, f"Expected fallback for invalid level but got '{progress}'"
    print("✓ Test 2 passed: Invalid level (99) handled")

    # Test Case 3: Invalid level (zero)
    progress = runner._get_level_progress_indicator(imp, 0)
    assert '○ → ○ → ○' in progress, f"Expected fallback for zero level but got '{progress}'"
    print("✓ Test 3 passed: Invalid level (0) handled")

    # Test Case 4: Invalid level (negative)
    progress = runner._get_level_progress_indicator(imp, -1)
    assert '○ → ○ → ○' in progress, f"Expected fallback for negative level but got '{progress}'"
    print("✓ Test 4 passed: Invalid level (-1) handled")

    # Test Case 5: Empty dict
    progress = runner._get_level_progress_indicator({}, 1)
    assert '○ → ○ → ○' in progress, f"Expected fallback for empty dict but got '{progress}'"
    print("✓ Test 5 passed: Empty dict handled")

    # Test Case 6: Invalid type (string instead of dict)
    progress = runner._get_level_progress_indicator("not a dict", 1)
    assert '○ → ○ → ○' in progress, f"Expected fallback for string input but got '{progress}'"
    print("✓ Test 6 passed: Invalid type (string) handled")

    # Test Case 7: Invalid type (list instead of dict)
    progress = runner._get_level_progress_indicator([1, 2, 3], 1)
    assert '○ → ○ → ○' in progress, f"Expected fallback for list input but got '{progress}'"
    print("✓ Test 7 passed: Invalid type (list) handled")

    # Clean up
    test_db_path.unlink()

    print("\n✓ All edge case tests passed!")
    return True


def test_all_three_levels():
    """Test progress indicator for all three levels (MVP, Enhanced, Advanced)."""
    print("\nTesting all three levels...")

    test_db_path = Path("/tmp/test_dashboard_levels.db")
    if test_db_path.exists():
        test_db_path.unlink()

    db = Database(test_db_path)
    runner = Runner(Path("/tmp"))
    runner.db = db

    imp_id = db.add(title="Test Feature", description="Test", priority=50)

    # Test MVP level
    db.save_plan(imp_id, 1, "MVP plan")
    imp = db.get_by_id(imp_id)
    progress = runner._get_level_progress_indicator(imp, 1)
    assert '●' in progress and '→' in progress, f"MVP level failed: '{progress}'"
    print("✓ Test 1 passed: MVP level progress indicator works")

    # Test Enhanced level
    db.save_plan(imp_id, 2, "Enhanced plan")
    imp = db.get_by_id(imp_id)
    progress = runner._get_level_progress_indicator(imp, 2)
    assert '●' in progress and '→' in progress, f"Enhanced level failed: '{progress}'"
    print("✓ Test 2 passed: Enhanced level progress indicator works")

    # Test Advanced level
    db.save_plan(imp_id, 3, "Advanced plan")
    imp = db.get_by_id(imp_id)
    progress = runner._get_level_progress_indicator(imp, 3)
    assert '●' in progress and '→' in progress, f"Advanced level failed: '{progress}'"
    print("✓ Test 3 passed: Advanced level progress indicator works")

    # Clean up
    test_db_path.unlink()

    print("\n✓ All level tests passed!")
    return True


def test_input_validation():
    """Test input validation and helpful error messages."""
    print("\nTesting input validation...")

    test_db_path = Path("/tmp/test_validation.db")
    if test_db_path.exists():
        test_db_path.unlink()

    db = Database(test_db_path)
    runner = Runner(Path("/tmp"))
    runner.db = db

    # Test Case 1: Valid input with level 1
    imp_id = db.add(title="Valid Test", description="Test", priority=50)
    imp = db.get_by_id(imp_id)
    progress = runner._get_level_progress_indicator(imp, 1)
    assert isinstance(progress, str), "Progress indicator should return string"
    assert len(progress) > 0, "Progress indicator should not be empty"
    print("✓ Test 1 passed: Valid input returns non-empty string")

    # Test Case 2: Check HTML structure
    assert '<span class="level-progress">' in progress, "Missing HTML wrapper"
    assert '</span>' in progress, "Missing closing HTML tag"
    print("✓ Test 2 passed: HTML structure is valid")

    # Test Case 3: Check symbols are present
    symbols_present = any(symbol in progress for symbol in ['○', '●', '✓', '✗'])
    assert symbols_present, "No progress symbols found in output"
    print("✓ Test 3 passed: Progress symbols present")

    # Test Case 4: Check arrows are present
    assert '→' in progress, "Arrow separator missing"
    print("✓ Test 4 passed: Arrow separator present")

    # Clean up
    test_db_path.unlink()

    print("\n✓ All validation tests passed!")
    return True


def test_complete_workflow():
    """Test a complete workflow from pending to passed."""
    print("\nTesting complete workflow...")

    test_db_path = Path("/tmp/test_workflow.db")
    if test_db_path.exists():
        test_db_path.unlink()

    db = Database(test_db_path)
    runner = Runner(Path("/tmp"))
    runner.db = db

    imp_id = db.add(title="Workflow Test", description="Test", priority=50)

    # Stage 1: Initial state (no plan)
    imp = db.get_by_id(imp_id)
    progress = runner._get_level_progress_indicator(imp, 1)
    assert progress.count('○') == 3, f"Expected 3 empty circles but got '{progress}'"
    print("✓ Stage 1: Initial state (○ → ○ → ○)")

    # Stage 2: Add plan
    db.save_plan(imp_id, 1, "Test plan")
    imp = db.get_by_id(imp_id)
    progress = runner._get_level_progress_indicator(imp, 1)
    assert progress.count('●') == 1 and progress.count('○') == 2, f"Expected 1 filled, 2 empty but got '{progress}'"
    print("✓ Stage 2: Plan added (● → ○ → ○)")

    # Stage 3: Add output
    db.mark_level_completed(imp_id, 1, "Test output")
    imp = db.get_by_id(imp_id)
    progress = runner._get_level_progress_indicator(imp, 1)
    assert progress.count('●') == 2 and progress.count('○') == 1, f"Expected 2 filled, 1 empty but got '{progress}'"
    print("✓ Stage 3: Output added (● → ● → ○)")

    # Stage 4: Mark test passed
    db.mark_test_passed(imp_id, 1, "Test passed")
    imp = db.get_by_id(imp_id)
    progress = runner._get_level_progress_indicator(imp, 1)
    assert '✓' in progress and progress.count('●') == 2, f"Expected checkmark but got '{progress}'"
    print("✓ Stage 4: Test passed (● → ● → ✓)")

    # Clean up
    test_db_path.unlink()

    print("\n✓ Complete workflow test passed!")
    return True


def test_test_failure_scenario():
    """Test test failure scenario."""
    print("\nTesting test failure scenario...")

    test_db_path = Path("/tmp/test_failure.db")
    if test_db_path.exists():
        test_db_path.unlink()

    db = Database(test_db_path)
    runner = Runner(Path("/tmp"))
    runner.db = db

    imp_id = db.add(title="Failure Test", description="Test", priority=50)

    # Set up completed state
    db.save_plan(imp_id, 1, "Test plan")
    db.mark_level_completed(imp_id, 1, "Test output")

    # Mark test as failed
    db.mark_test_failed(imp_id, 1, "Test failed")
    imp = db.get_by_id(imp_id)
    progress = runner._get_level_progress_indicator(imp, 1)

    assert '✗' in progress, f"Expected X symbol for failure but got '{progress}'"
    assert progress.count('●') == 2, f"Expected 2 filled circles but got '{progress}'"
    print("✓ Test failure scenario: (● → ● → ✗)")

    # Clean up
    test_db_path.unlink()

    print("\n✓ Test failure scenario passed!")
    return True


if __name__ == '__main__':
    try:
        # Run all MVP tests first
        print("="*50)
        print("Running MVP tests first...")
        print("="*50)

        import test_dashboard_progress_mvp
        test_dashboard_progress_mvp.test_level_progress_indicator()
        test_dashboard_progress_mvp.test_dashboard_html_generation()

        print("\n" + "="*50)
        print("MVP tests passed! Now running Enhanced tests...")
        print("="*50 + "\n")

        # Run Enhanced tests
        test_edge_cases()
        test_all_three_levels()
        test_input_validation()
        test_complete_workflow()
        test_test_failure_scenario()

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
