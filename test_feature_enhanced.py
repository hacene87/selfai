#!/usr/bin/env python3
"""
Test Feature Suite - Enhanced Tests

This test suite validates the SelfAI planning-first workflow with comprehensive coverage:

**Enhanced Tests:**
- Planning workflow (pending -> planning -> plan_review -> approved)
- User feedback loop
- Parallel task limit enforcement
- 3-failure cancellation logic
- Task re-enabling with feedback
- Database query methods
- Timestamp tracking

**Usage:**
  python test_feature_enhanced.py

**Expected Results:**
All tests should pass, validating that the planning-first workflow
operates correctly across all phases and edge cases.
"""
import sys
import tempfile
import json
from pathlib import Path
from datetime import datetime

# Add selfai to path
sys.path.insert(0, str(Path(__file__).parent))

from selfai.database import Database, MAX_TEST_ATTEMPTS, MAX_PARALLEL_TASKS, VALID_STATUSES


def test_planning_workflow():
    """Test complete planning workflow: pending -> planning -> plan_review -> approved."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        db_path = Path(tmp.name)
    try:
        db = Database(db_path)
        imp_id = db.add("Test Planning Feature", "Validate planning workflow")

        # Phase 1: Mark as planning
        db.mark_planning(imp_id)
        imp = db.get_by_id(imp_id)
        assert imp['status'] == 'planning', f"Expected planning, got {imp['status']}"

        # Phase 2: Save plan and verify it's auto-approved
        plan_json = json.dumps({"description": "Test plan", "files_to_modify": ["test.py"]})
        db.save_plan(imp_id, plan_json)
        imp = db.get_by_id(imp_id)
        assert imp['status'] == 'approved', f"Expected approved (auto-approved), got {imp['status']}"
        assert imp['plan_content'] == plan_json
        assert imp['plan_status'] == 'approved'

        return True
    finally:
        db_path.unlink()


def test_feedback_loop():
    """Test user feedback on plans."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        db_path = Path(tmp.name)
    try:
        db = Database(db_path)
        imp_id = db.add("Feedback Test", "Test feedback functionality")

        # Generate and save plan
        db.mark_planning(imp_id)
        plan_json = json.dumps({"description": "Initial plan"})
        db.save_plan(imp_id, plan_json)

        # Request feedback
        feedback = "Please add error handling and logging"
        db.request_plan_feedback(imp_id, feedback)

        imp = db.get_by_id(imp_id)
        assert imp['status'] == 'pending', f"Expected pending after feedback, got {imp['status']}"
        assert imp['plan_status'] == 'needs_feedback'
        assert imp['user_feedback'] == feedback

        # Re-plan with feedback
        db.mark_planning(imp_id)
        updated_plan = json.dumps({"description": "Updated plan with error handling"})
        db.save_plan(imp_id, updated_plan)

        imp = db.get_by_id(imp_id)
        assert imp['status'] == 'approved'
        assert imp['plan_content'] == updated_plan

        return True
    finally:
        db_path.unlink()


def test_parallel_task_limits():
    """Verify MAX_PARALLEL_TASKS limit is enforced."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        db_path = Path(tmp.name)
    try:
        db = Database(db_path)

        # Create more tasks than the limit
        task_ids = []
        for i in range(5):
            task_id = db.add(f"Task {i+1}", f"Description {i+1}")
            task_ids.append(task_id)
            if i < MAX_PARALLEL_TASKS:
                db.mark_in_progress(task_id)

        # Verify active count
        active_count = db.get_active_count()
        assert active_count == MAX_PARALLEL_TASKS, f"Expected {MAX_PARALLEL_TASKS} active, got {active_count}"

        # Check if can start new task
        can_start = db.can_start_new_task()
        assert not can_start, "Should not allow new tasks when at limit"

        # Complete one task
        db.mark_testing(task_ids[0], "output")
        db.mark_test_passed(task_ids[0], "test passed")

        # Now should be able to start new task
        can_start = db.can_start_new_task()
        assert can_start, "Should allow new task after completion"

        return True
    finally:
        db_path.unlink()


def test_max_test_failures():
    """Test that tasks are cancelled after MAX_TEST_ATTEMPTS failures."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        db_path = Path(tmp.name)
    try:
        db = Database(db_path)
        imp_id = db.add("Failing Feature", "Will fail 3 times")

        # Setup for testing
        db.mark_in_progress(imp_id)
        db.mark_testing(imp_id, "implementation output")

        # Fail 3 times
        for i in range(MAX_TEST_ATTEMPTS):
            db.mark_test_failed(imp_id, f"Test failure #{i+1}")
            imp = db.get_by_id(imp_id)

            if i < MAX_TEST_ATTEMPTS - 1:
                assert imp['status'] == 'failed', f"Expected failed status on attempt {i+1}"
                assert imp['test_count'] == i + 1
            else:
                # On 3rd failure, should be cancelled
                assert imp['status'] == 'cancelled', f"Expected cancelled after {MAX_TEST_ATTEMPTS} failures"
                assert 'Cancelled after 3 test failures' in (imp['error'] or '')

        return True
    finally:
        db_path.unlink()


def test_re_enable_cancelled():
    """Test re-enabling a cancelled task with feedback."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        db_path = Path(tmp.name)
    try:
        db = Database(db_path)
        imp_id = db.add("Cancelled Task", "Will be cancelled then re-enabled")

        # Simulate cancellation
        db.mark_in_progress(imp_id)
        db.mark_testing(imp_id, "output")
        for _ in range(MAX_TEST_ATTEMPTS):
            db.mark_test_failed(imp_id, "failure")

        imp = db.get_by_id(imp_id)
        assert imp['status'] == 'cancelled'

        # Re-enable with feedback
        feedback = "Updated requirements: use different approach"
        db.re_enable_cancelled(imp_id, feedback)

        imp = db.get_by_id(imp_id)
        assert imp['status'] == 'pending', f"Expected pending, got {imp['status']}"
        assert imp['user_feedback'] == feedback
        assert imp['test_count'] == 0, "Test count should be reset"
        assert imp['plan_status'] == 'needs_feedback'

        return True
    finally:
        db_path.unlink()


def test_query_methods():
    """Test various database query methods."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        db_path = Path(tmp.name)
    try:
        db = Database(db_path)

        # Create tasks in different states
        pending_id = db.add("Pending Task", "Needs planning")

        planning_id = db.add("Planning Task", "Currently planning")
        db.mark_planning(planning_id)

        approved_id = db.add("Approved Task", "Ready to execute")
        plan = json.dumps({"description": "plan", "files_to_modify": []})
        db.mark_planning(approved_id)
        db.save_plan(approved_id, plan)

        # Test query methods
        pending = db.get_pending_planning()
        assert len(pending) > 0 and any(t['id'] == pending_id for t in pending), \
            "Should find pending task in get_pending_planning()"

        approved = db.get_approved_tasks()
        assert len(approved) > 0 and any(t['id'] == approved_id for t in approved), \
            "Should find approved task in get_approved_tasks()"

        return True
    finally:
        db_path.unlink()


def test_timestamp_tracking():
    """Verify timestamps are properly set during lifecycle."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        db_path = Path(tmp.name)
    try:
        db = Database(db_path)
        imp_id = db.add("Timestamp Test", "Validate timestamps")

        imp = db.get_by_id(imp_id)
        # created_at should be set
        assert imp['created_at'] is not None
        created_time = datetime.fromisoformat(imp['created_at'])

        # started_at should be None initially
        assert imp['started_at'] is None

        # Mark in progress
        db.mark_in_progress(imp_id)
        imp = db.get_by_id(imp_id)
        assert imp['started_at'] is not None
        started_time = datetime.fromisoformat(imp['started_at'])
        assert started_time >= created_time

        # completed_at should be None until completion
        assert imp['completed_at'] is None

        # Complete the task
        db.mark_testing(imp_id, "output")
        db.mark_test_passed(imp_id, "passed")
        imp = db.get_by_id(imp_id)
        assert imp['completed_at'] is not None
        completed_time = datetime.fromisoformat(imp['completed_at'])
        assert completed_time >= started_time

        return True
    finally:
        db_path.unlink()


def main():
    """Run all enhanced tests with detailed reporting."""
    tests = [
        ("Planning Workflow", test_planning_workflow),
        ("Feedback Loop", test_feedback_loop),
        ("Parallel Task Limits", test_parallel_task_limits),
        ("Max Test Failures", test_max_test_failures),
        ("Re-enable Cancelled", test_re_enable_cancelled),
        ("Query Methods", test_query_methods),
        ("Timestamp Tracking", test_timestamp_tracking),
    ]

    print("=" * 70)
    print("Test Feature Enhanced - Running Comprehensive Tests")
    print("=" * 70)

    passed = 0
    failed = 0
    failures = []

    for name, test_func in tests:
        print(f"\nRunning: {name}...", end=" ")
        try:
            result = test_func()
            if result:
                print("✓ PASS")
                passed += 1
            else:
                print("✗ FAIL")
                failed += 1
                failures.append(name)
        except Exception as e:
            print(f"✗ FAIL - {e}")
            failed += 1
            failures.append(f"{name}: {str(e)}")

    print("\n" + "=" * 70)
    print(f"Results: {passed} passed, {failed} failed")
    if failures:
        print("\nFailed tests:")
        for failure in failures:
            print(f"  - {failure}")
    print("=" * 70)

    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
