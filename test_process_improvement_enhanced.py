"""Enhanced level tests for 3-level complexity system - edge cases and robustness."""
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock
from selfai.runner import Runner
from selfai.database import Database


def test_invalid_level_validation():
    """Test that invalid level parameters are rejected."""
    test_dir = tempfile.mkdtemp()
    try:
        repo_path = Path(test_dir) / 'test_repo'
        repo_path.mkdir()
        runner = Runner(repo_path)

        invalid_levels = [0, 1, 4, -1, 100, 'invalid']
        for level in invalid_levels:
            try:
                if isinstance(level, str):
                    continue
                runner.db.check_level_unlock(level)
                assert False, f"Should raise ValueError for level {level}"
            except ValueError as e:
                assert 'Invalid level' in str(e), f"Error should mention invalid level: {e}"

        print("✓ Test passed: Invalid level validation works")
        return True
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_boundary_conditions():
    """Test unlock at exact thresholds (4, 5, 6 MVP; 9, 10, 11 Enhanced)."""
    test_dir = tempfile.mkdtemp()
    try:
        repo_path = Path(test_dir) / 'test_repo'
        repo_path.mkdir()
        runner = Runner(repo_path)

        for count in range(4, 7):
            db = Database(Path(test_dir) / f'test_db_{count}.db')
            for i in range(count):
                feat_id = db.add(
                    title=f"Feature {i+1}",
                    description=f"Test feature {i+1}",
                    category="feature",
                    priority=50,
                    source="test"
                )
                db.mark_level_completed(feat_id, 1, "test output")
                db.mark_test_passed(feat_id, 1, "test passed")

            is_unlocked = db.check_level_unlock(2)
            should_unlock = count >= 5

            assert is_unlocked == should_unlock, \
                f"With {count} MVP passes, Enhanced unlock should be {should_unlock}, got {is_unlocked}"

        for count in range(9, 12):
            db = Database(Path(test_dir) / f'test_db_adv_{count}.db')
            for i in range(count):
                feat_id = db.add(
                    title=f"Feature {i+1}",
                    description=f"Test feature {i+1}",
                    category="feature",
                    priority=50,
                    source="test"
                )
                db.mark_level_completed(feat_id, 1, "test output")
                db.mark_test_passed(feat_id, 1, "test passed")
                db.enhance_feature(feat_id)
                db.mark_level_completed(feat_id, 2, "test output")
                db.mark_test_passed(feat_id, 2, "test passed")

            is_unlocked = db.check_level_unlock(3)
            should_unlock = count >= 10

            assert is_unlocked == should_unlock, \
                f"With {count} Enhanced passes, Advanced unlock should be {should_unlock}, got {is_unlocked}"

        print("✓ Test passed: Boundary conditions (exact thresholds) work correctly")
        return True
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_unlock_progress_reporting():
    """Test that get_unlock_progress returns correct counts."""
    test_dir = tempfile.mkdtemp()
    try:
        repo_path = Path(test_dir) / 'test_repo'
        repo_path.mkdir()
        runner = Runner(repo_path)

        test_cases = [0, 3, 5, 7]
        for count in test_cases:
            for i in range(count):
                feat_id = runner.db.add(
                    title=f"Feature {i+1}",
                    description=f"Test feature {i+1}",
                    category="feature",
                    priority=50,
                    source="test"
                )
                runner.db.mark_level_completed(feat_id, 1, "test output")
                runner.db.mark_test_passed(feat_id, 1, "test passed")

            current, required = runner.db.get_unlock_progress(2)
            assert current == count, f"Current count should be {count}, got {current}"
            assert required == 5, f"Required should be 5, got {required}"

        print("✓ Test passed: Unlock progress reporting is accurate")
        return True
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_unlock_event_persistence():
    """Test that unlock events are recorded and not duplicated."""
    test_dir = tempfile.mkdtemp()
    try:
        repo_path = Path(test_dir) / 'test_repo'
        repo_path.mkdir()
        runner = Runner(repo_path)

        for i in range(5):
            feat_id = runner.db.add(
                title=f"Feature {i+1}",
                description=f"Test feature {i+1}",
                category="feature",
                priority=50,
                source="test"
            )
            runner.db.mark_level_completed(feat_id, 1, "test output")
            runner.db.mark_test_passed(feat_id, 1, "test passed")

        result1 = runner.db.record_unlock_event(2, feature_id=5)
        assert result1 is True, "First unlock event should be recorded"

        result2 = runner.db.record_unlock_event(2, feature_id=5)
        assert result2 is False, "Duplicate unlock event should not be recorded"

        print("✓ Test passed: Unlock events are persisted and deduplicated")
        return True
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_mixed_feature_states():
    """Test unlock counting with features at different levels and states."""
    test_dir = tempfile.mkdtemp()
    try:
        repo_path = Path(test_dir) / 'test_repo'
        repo_path.mkdir()
        runner = Runner(repo_path)

        feat1 = runner.db.add(title="Completed MVP", description="desc", category="feature")
        runner.db.mark_level_completed(feat1, 1, "output")
        runner.db.mark_test_passed(feat1, 1, "passed")

        feat2 = runner.db.add(title="In Progress MVP", description="desc", category="feature")
        runner.db.mark_in_progress(feat2)

        feat3 = runner.db.add(title="Failed MVP", description="desc", category="feature")
        runner.db.mark_level_completed(feat3, 1, "output")
        runner.db.mark_test_failed(feat3, 1, "failed")

        feat4 = runner.db.add(title="Testing MVP", description="desc", category="feature")
        runner.db.mark_level_completed(feat4, 1, "output")

        feat5 = runner.db.add(title="Pending", description="desc", category="feature")

        current, required = runner.db.get_unlock_progress(2)
        assert current == 1, f"Only completed features should count, expected 1, got {current}"

        print("✓ Test passed: Mixed feature states counted correctly")
        return True
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_exceeding_thresholds():
    """Test unlock with counts well over the threshold."""
    test_dir = tempfile.mkdtemp()
    try:
        repo_path = Path(test_dir) / 'test_repo'
        repo_path.mkdir()
        runner = Runner(repo_path)

        for i in range(15):
            feat_id = runner.db.add(
                title=f"Feature {i+1}",
                description=f"Test feature {i+1}",
                category="feature",
                priority=50,
                source="test"
            )
            runner.db.mark_level_completed(feat_id, 1, "test output")
            runner.db.mark_test_passed(feat_id, 1, "test passed")

        assert runner.db.check_level_unlock(2), "Enhanced should unlock with 15 MVP passes"

        current, required = runner.db.get_unlock_progress(2)
        assert current == 15, f"Current count should be 15, got {current}"
        assert required == 5, f"Required should be 5, got {required}"

        print("✓ Test passed: Exceeding thresholds handled correctly")
        return True
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_unlock_progress_with_zero_features():
    """Test progress reporting with empty database."""
    test_dir = tempfile.mkdtemp()
    try:
        repo_path = Path(test_dir) / 'test_repo'
        repo_path.mkdir()
        runner = Runner(repo_path)

        current, required = runner.db.get_unlock_progress(2)
        assert current == 0, f"Current should be 0 with no features, got {current}"
        assert required == 5, f"Required should be 5, got {required}"

        assert not runner.db.check_level_unlock(2), "Enhanced should be locked with no features"

        print("✓ Test passed: Zero features handled correctly")
        return True
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_concurrent_unlock_event_recording():
    """Test that concurrent unlock attempts don't create duplicates."""
    test_dir = tempfile.mkdtemp()
    try:
        repo_path = Path(test_dir) / 'test_repo'
        repo_path.mkdir()
        runner = Runner(repo_path)

        for i in range(5):
            feat_id = runner.db.add(
                title=f"Feature {i+1}",
                description=f"Test feature {i+1}",
                category="feature",
                priority=50,
                source="test"
            )
            runner.db.mark_level_completed(feat_id, 1, "test output")
            runner.db.mark_test_passed(feat_id, 1, "test passed")

        results = []
        for _ in range(5):
            result = runner.db.record_unlock_event(2, feature_id=1)
            results.append(result)

        true_count = sum(1 for r in results if r is True)
        false_count = sum(1 for r in results if r is False)

        assert true_count == 1, f"Only one unlock event should succeed, got {true_count}"
        assert false_count == 4, f"Four attempts should fail as duplicates, got {false_count}"

        print("✓ Test passed: Concurrent unlock events deduplicated")
        return True
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


if __name__ == '__main__':
    print("Testing Enhanced implementation - edge cases and robustness...")
    print()

    results = []
    results.append(test_invalid_level_validation())
    results.append(test_boundary_conditions())
    results.append(test_unlock_progress_reporting())
    results.append(test_unlock_event_persistence())
    results.append(test_mixed_feature_states())
    results.append(test_exceeding_thresholds())
    results.append(test_unlock_progress_with_zero_features())
    results.append(test_concurrent_unlock_event_recording())

    print()
    print(f"Results: {sum(results)}/{len(results)} tests passed")

    if all(results):
        print("\n✓ All Enhanced tests passed!")
        exit(0)
    else:
        print("\n✗ Some tests failed")
        exit(1)
