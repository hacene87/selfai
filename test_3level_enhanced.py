"""Enhanced level tests for 3-Level Complexity System.

These tests validate robust functionality:
- Level unlock logic works correctly
- Features correctly advance from level 1 → 2 → 3
- Test counts track separately per level
- Edge cases like exact threshold unlock
"""
import tempfile
from pathlib import Path
import sys

def test_enhanced_unlocks_after_5_mvps():
    """Test that Enhanced level unlocks after 5 completed MVP features."""
    from selfai.database import Database

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / 'test.db')

        # Initially Enhanced should be locked
        unlocked, msg = db.is_level_unlocked(2)
        assert not unlocked, "Enhanced should be locked initially"
        assert '5' in msg, f"Message should mention requirement of 5, got: {msg}"

        # Add and complete 4 MVP features
        for i in range(4):
            imp_id = db.add(title=f'Feature {i+1}', description='Test')
            db.mark_level_test_passed(imp_id, 1, 'Test passed')

        # Still locked after 4
        db.check_and_unlock_levels()
        unlocked, _ = db.is_level_unlocked(2)
        assert not unlocked, "Enhanced should still be locked after 4 MVPs"

        # Add 5th feature and complete it
        imp_id = db.add(title='Feature 5', description='Test')
        db.mark_level_test_passed(imp_id, 1, 'Test passed')

        # Now should be unlocked
        db.check_and_unlock_levels()
        unlocked, msg = db.is_level_unlocked(2)
        assert unlocked, "Enhanced should be unlocked after 5 MVPs"

        print("✓ Enhanced level unlocks after exactly 5 completed MVP features")


def test_advanced_unlocks_after_10_enhanced():
    """Test that Advanced level unlocks after 10 completed Enhanced features."""
    from selfai.database import Database

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / 'test.db')

        # Unlock Enhanced first (need 5 MVPs)
        for i in range(5):
            imp_id = db.add(title=f'MVP {i+1}', description='Test')
            db.mark_level_test_passed(imp_id, 1, 'Test passed')

        db.check_and_unlock_levels()

        # Initially Advanced should be locked
        unlocked, msg = db.is_level_unlocked(3)
        assert not unlocked, "Advanced should be locked initially"

        # Complete 9 Enhanced features
        for i in range(9):
            imp_id = db.add(title=f'Enhanced {i+1}', description='Test')
            # Set to level 2 and complete
            db.get_connection().__enter__().execute(
                'UPDATE improvements SET current_level = 2, enhanced_status = "completed" WHERE id = ?',
                (imp_id,)
            )

        db.check_and_unlock_levels()
        unlocked, _ = db.is_level_unlocked(3)
        assert not unlocked, "Advanced should still be locked after 9 Enhanced"

        # Complete 10th Enhanced feature
        imp_id = db.add(title='Enhanced 10', description='Test')
        with db.get_connection() as conn:
            conn.execute(
                'UPDATE improvements SET current_level = 2, enhanced_status = "completed" WHERE id = ?',
                (imp_id,)
            )

        db.check_and_unlock_levels()
        unlocked, msg = db.is_level_unlocked(3)
        assert unlocked, "Advanced should be unlocked after 10 Enhanced"

        print("✓ Advanced level unlocks after exactly 10 completed Enhanced features")


def test_feature_advances_through_levels():
    """Test that a feature correctly advances from level 1 → 2 → 3."""
    from selfai.database import Database

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / 'test.db')

        # Create a feature
        imp_id = db.add(title='Progressive Feature', description='Test')
        task = db.get_by_id(imp_id)

        # Should start at level 1
        assert task['current_level'] == 1, "Should start at level 1"

        # Pass MVP tests and advance
        db.mark_level_test_passed(imp_id, 1, 'MVP tests passed')
        db.advance_to_next_level(imp_id)

        task = db.get_by_id(imp_id)
        assert task['current_level'] == 2, "Should be at level 2 after MVP completion"
        assert task['mvp_status'] == 'completed', "MVP should be completed"
        assert task['enhanced_status'] == 'pending', "Enhanced should be pending"

        # Pass Enhanced tests and advance
        db.mark_level_test_passed(imp_id, 2, 'Enhanced tests passed')
        db.advance_to_next_level(imp_id)

        task = db.get_by_id(imp_id)
        assert task['current_level'] == 3, "Should be at level 3 after Enhanced completion"
        assert task['enhanced_status'] == 'completed', "Enhanced should be completed"
        assert task['advanced_status'] == 'pending', "Advanced should be pending"

        # Pass Advanced tests - should mark as fully complete
        db.mark_level_test_passed(imp_id, 3, 'Advanced tests passed')

        task = db.get_by_id(imp_id)
        assert task['advanced_status'] == 'completed', "Advanced should be completed"
        assert task['status'] == 'completed', "Overall status should be completed"

        print("✓ Feature correctly advances through all 3 levels")


def test_level_test_counts_are_separate():
    """Test that test counts track separately for each level."""
    from selfai.database import Database

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / 'test.db')

        imp_id = db.add(title='Test Feature', description='Test')

        # Initially all counts should be 0
        task = db.get_by_id(imp_id)
        assert task['mvp_test_count'] == 0, "MVP test count should start at 0"
        assert task['enhanced_test_count'] == 0, "Enhanced test count should start at 0"
        assert task['advanced_test_count'] == 0, "Advanced test count should start at 0"

        # Increment MVP test count by setting it in database
        with db.get_connection() as conn:
            conn.execute('UPDATE improvements SET mvp_test_count = 2 WHERE id = ?', (imp_id,))

        task = db.get_by_id(imp_id)
        assert task['mvp_test_count'] == 2, "MVP test count should be 2"
        assert task['enhanced_test_count'] == 0, "Enhanced test count should still be 0"
        assert task['advanced_test_count'] == 0, "Advanced test count should still be 0"

        print("✓ Test counts track separately for each level")


def test_get_features_for_level():
    """Test that get_features_for_level returns correct features."""
    from selfai.database import Database

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / 'test.db')

        # Create features at different levels
        mvp_id = db.add(title='MVP Feature', description='Test')
        with db.get_connection() as conn:
            conn.execute('UPDATE improvements SET current_level = 1, mvp_status = "approved" WHERE id = ?', (mvp_id,))

        enhanced_id = db.add(title='Enhanced Feature', description='Test')
        with db.get_connection() as conn:
            conn.execute('UPDATE improvements SET current_level = 2, enhanced_status = "approved" WHERE id = ?', (enhanced_id,))

        # Get features for each level
        mvp_features = db.get_features_for_level(1)
        enhanced_features = db.get_features_for_level(2)

        assert len(mvp_features) == 1, f"Should have 1 MVP feature, got {len(mvp_features)}"
        assert mvp_features[0]['id'] == mvp_id, "Should get the MVP feature"

        assert len(enhanced_features) == 1, f"Should have 1 Enhanced feature, got {len(enhanced_features)}"
        assert enhanced_features[0]['id'] == enhanced_id, "Should get the Enhanced feature"

        print("✓ get_features_for_level returns correct features for each level")


if __name__ == '__main__':
    print("\n=== Running Enhanced Level Tests ===\n")

    tests = [
        test_enhanced_unlocks_after_5_mvps,
        test_advanced_unlocks_after_10_enhanced,
        test_feature_advances_through_levels,
        test_level_test_counts_are_separate,
        test_get_features_for_level
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
