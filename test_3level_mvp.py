"""MVP level tests for 3-Level Complexity System.

These tests validate basic functionality:
- LEVEL_GUIDANCE exists with all 3 levels
- Database schema has level tracking columns
- MVP level is always unlocked
- New improvements start at level 1
"""
import tempfile
from pathlib import Path
import sys

def test_level_guidance_exists():
    """Test that LEVEL_GUIDANCE is defined with all 3 levels."""
    from selfai.runner import LEVEL_GUIDANCE

    assert 1 in LEVEL_GUIDANCE, "MVP level (1) not found in LEVEL_GUIDANCE"
    assert 2 in LEVEL_GUIDANCE, "Enhanced level (2) not found in LEVEL_GUIDANCE"
    assert 3 in LEVEL_GUIDANCE, "Advanced level (3) not found in LEVEL_GUIDANCE"

    # Check basic structure
    for level in [1, 2, 3]:
        assert 'name' in LEVEL_GUIDANCE[level], f"Level {level} missing 'name'"
        assert 'description' in LEVEL_GUIDANCE[level], f"Level {level} missing 'description'"
        assert 'scope' in LEVEL_GUIDANCE[level], f"Level {level} missing 'scope'"
        assert 'test_criteria' in LEVEL_GUIDANCE[level], f"Level {level} missing 'test_criteria'"
        assert 'prompt_suffix' in LEVEL_GUIDANCE[level], f"Level {level} missing 'prompt_suffix'"

    print("✓ All 3 levels defined in LEVEL_GUIDANCE with required fields")


def test_database_has_level_columns():
    """Test that database schema includes level tracking columns."""
    from selfai.database import Database

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / 'test.db')
        imp_id = db.add(title='Test Feature', description='Test')
        task = db.get_by_id(imp_id)

        # Check all required columns exist
        required_cols = [
            'current_level',
            'mvp_status', 'mvp_output', 'mvp_test_output', 'mvp_test_count',
            'enhanced_status', 'enhanced_output', 'enhanced_test_output', 'enhanced_test_count',
            'advanced_status', 'advanced_output', 'advanced_test_output', 'advanced_test_count'
        ]

        for col in required_cols:
            assert col in task, f"Column '{col}' not found in database schema"

        print("✓ Level columns exist in database schema")


def test_mvp_always_unlocked():
    """Test that MVP level is always unlocked."""
    from selfai.database import Database

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / 'test.db')
        unlocked, msg = db.is_level_unlocked(1)

        assert unlocked == True, "MVP level should always be unlocked"
        assert 'available' in msg.lower(), f"Unexpected message: {msg}"

        print("✓ MVP level is always unlocked")


def test_new_improvements_start_at_level_1():
    """Test that new improvements start at level 1 (MVP)."""
    from selfai.database import Database

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / 'test.db')
        imp_id = db.add(title='Test Feature', description='Test')
        task = db.get_by_id(imp_id)

        assert task['current_level'] == 1, f"New improvement should start at level 1, got {task['current_level']}"
        assert task['mvp_status'] == 'pending', f"MVP status should be 'pending', got {task['mvp_status']}"
        assert task['enhanced_status'] == 'locked', f"Enhanced status should be 'locked', got {task['enhanced_status']}"
        assert task['advanced_status'] == 'locked', f"Advanced status should be 'locked', got {task['advanced_status']}"

        print("✓ New improvements start at level 1 with correct initial statuses")


def test_level_unlocks_table_exists():
    """Test that level_unlocks table is created and initialized."""
    from selfai.database import Database
    import sqlite3

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / 'test.db')

        with sqlite3.connect(db.db_path) as conn:
            cursor = conn.execute("SELECT level, required_count FROM level_unlocks ORDER BY level")
            rows = cursor.fetchall()

            assert len(rows) == 2, f"Should have 2 unlock entries, got {len(rows)}"

            enhanced = [r for r in rows if r[0] == 'enhanced']
            assert len(enhanced) == 1, "Enhanced level unlock entry not found"
            assert enhanced[0][1] == 5, f"Enhanced should require 5 MVPs, got {enhanced[0][1]}"

            advanced = [r for r in rows if r[0] == 'advanced']
            assert len(advanced) == 1, "Advanced level unlock entry not found"
            assert advanced[0][1] == 10, f"Advanced should require 10 Enhanced, got {advanced[0][1]}"

        print("✓ Level unlocks table exists with correct thresholds")


if __name__ == '__main__':
    print("\n=== Running MVP Level Tests ===\n")

    tests = [
        test_level_guidance_exists,
        test_database_has_level_columns,
        test_mvp_always_unlocked,
        test_new_improvements_start_at_level_1,
        test_level_unlocks_table_exists
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"✗ {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"✗ {test.__name__}: Unexpected error: {e}")
            failed += 1

    print(f"\n=== Results: {passed} passed, {failed} failed ===")

    if failed > 0:
        sys.exit(1)
