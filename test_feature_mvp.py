#!/usr/bin/env python3
"""MVP Test Feature - Basic validation of SelfAI core workflow."""
import sys
import tempfile
from pathlib import Path

# Add selfai to path
sys.path.insert(0, str(Path(__file__).parent))

from selfai.database import Database


def test_add_improvement():
    """Test adding an improvement to database."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        db_path = Path(tmp.name)

    try:
        db = Database(db_path)

        # Add improvement
        imp_id = db.add(
            title="Test Feature",
            description="Test description",
            category="test",
            priority=75
        )

        # Verify it was added
        assert imp_id > 0, "Failed to add improvement"

        # Verify retrieval
        imp = db.get_by_id(imp_id)
        assert imp is not None, "Failed to retrieve improvement"
        assert imp['title'] == "Test Feature", f"Wrong title: {imp['title']}"
        assert imp['description'] == "Test description", f"Wrong description: {imp['description']}"
        assert imp['category'] == "test", f"Wrong category: {imp['category']}"
        assert imp['priority'] == 75, f"Wrong priority: {imp['priority']}"
        assert imp['status'] == 'pending', f"Wrong status: {imp['status']}"
        assert imp['current_level'] == 1, f"Wrong level: {imp['current_level']}"

        return True
    finally:
        db_path.unlink()


def test_status_transitions():
    """Test status transitions work correctly."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        db_path = Path(tmp.name)

    try:
        db = Database(db_path)
        imp_id = db.add("Test Transitions", "Testing status changes")

        # pending -> in_progress
        db.mark_in_progress(imp_id)
        imp = db.get_by_id(imp_id)
        assert imp['status'] == 'in_progress', f"Expected in_progress, got {imp['status']}"

        # in_progress -> testing
        db.mark_level_completed(imp_id, level=1, output="MVP implementation complete")
        imp = db.get_by_id(imp_id)
        assert imp['status'] == 'testing', f"Expected testing, got {imp['status']}"
        assert imp['mvp_output'] == "MVP implementation complete", "Wrong output"

        # testing -> completed
        db.mark_test_passed(imp_id, level=1, test_output="All tests passed")
        imp = db.get_by_id(imp_id)
        assert imp['status'] == 'completed', f"Expected completed, got {imp['status']}"
        assert imp['mvp_test_status'] == 'passed', f"Expected passed test, got {imp['mvp_test_status']}"

        return True
    finally:
        db_path.unlink()


def test_plan_storage():
    """Test plan save/retrieve works."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        db_path = Path(tmp.name)

    try:
        db = Database(db_path)
        imp_id = db.add("Test Plan Storage", "Testing plan operations")

        # Save MVP plan
        mvp_plan = "1. Step one\n2. Step two\n3. Step three"
        result = db.save_plan(imp_id, level=1, plan=mvp_plan)
        assert result is True, "Failed to save plan"

        # Retrieve MVP plan
        retrieved_plan = db.get_plan(imp_id, level=1)
        assert retrieved_plan == mvp_plan, f"Plan mismatch: {retrieved_plan}"

        # Save Enhanced plan
        enhanced_plan = "Enhanced: 1. More features\n2. Better UX"
        db.save_plan(imp_id, level=2, plan=enhanced_plan)
        retrieved_enhanced = db.get_plan(imp_id, level=2)
        assert retrieved_enhanced == enhanced_plan, "Enhanced plan mismatch"

        # Verify MVP plan still intact
        mvp_check = db.get_plan(imp_id, level=1)
        assert mvp_check == mvp_plan, "MVP plan was corrupted"

        return True
    finally:
        db_path.unlink()


def test_stats_retrieval():
    """Test basic stats retrieval works."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        db_path = Path(tmp.name)

    try:
        db = Database(db_path)

        # Add multiple improvements with different statuses
        id1 = db.add("Feature 1", "Pending feature")
        id2 = db.add("Feature 2", "In progress feature")
        id3 = db.add("Feature 3", "Completed feature")

        db.mark_in_progress(id2)
        db.mark_level_completed(id3, level=1, output="Done")
        db.mark_test_passed(id3, level=1, test_output="Passed")

        # Get stats
        stats = db.get_stats()
        assert stats['total'] == 3, f"Expected 3 total, got {stats['total']}"
        assert stats['pending'] == 1, f"Expected 1 pending, got {stats['pending']}"
        assert stats['in_progress'] == 1, f"Expected 1 in_progress, got {stats['in_progress']}"
        assert stats['completed'] == 1, f"Expected 1 completed, got {stats['completed']}"

        return True
    finally:
        db_path.unlink()


def main():
    """Run all tests and report results."""
    tests = [
        ("Add Improvement", test_add_improvement),
        ("Status Transitions", test_status_transitions),
        ("Plan Storage", test_plan_storage),
        ("Stats Retrieval", test_stats_retrieval),
    ]

    print("=" * 60)
    print("Test Feature MVP - Running Tests")
    print("=" * 60)

    passed = 0
    failed = 0

    for name, test_func in tests:
        print(f"\nRunning: {name}...", end=" ")
        try:
            result = test_func()
            if result:
                print("PASS")
                passed += 1
            else:
                print("FAIL")
                failed += 1
        except Exception as e:
            print(f"FAIL - {e}")
            failed += 1

    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)

    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
