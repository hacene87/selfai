"""Enhanced tests for Autonomous Testing and Validation - error handling, validation, and edge cases."""
import tempfile
import shutil
import sqlite3
from pathlib import Path
import subprocess


def init_git_repo(path: Path):
    """Initialize a git repository."""
    subprocess.run(['git', 'init'], cwd=str(path), capture_output=True)
    subprocess.run(['git', 'config', 'user.name', 'Test User'], cwd=str(path), capture_output=True)
    subprocess.run(['git', 'config', 'user.email', 'test@test.com'], cwd=str(path), capture_output=True)
    test_file = path / 'README.md'
    test_file.write_text('# Test Repo\n')
    subprocess.run(['git', 'add', '.'], cwd=str(path), capture_output=True)
    subprocess.run(['git', 'commit', '-m', 'Initial commit'], cwd=str(path), capture_output=True)
    subprocess.run(['git', 'branch', '-M', 'main'], cwd=str(path), capture_output=True)


def test_parse_result_empty_output():
    """Test that empty output is handled gracefully."""
    from selfai.runner import Runner

    test_dir = tempfile.mkdtemp()
    try:
        repo_path = Path(test_dir) / 'test_repo'
        repo_path.mkdir()
        init_git_repo(repo_path)
        runner = Runner(repo_path)

        if not runner._parse_test_result(""):
            print("✓ Empty string correctly marked as failed")
        else:
            print("✗ Empty string should fail")
            return False

        if not runner._parse_test_result("   "):
            print("✓ Whitespace-only string correctly marked as failed")
        else:
            print("✗ Whitespace string should fail")
            return False

        return True
    except Exception as e:
        print(f"✗ Test failed: {e}")
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_parse_result_malformed_json():
    """Test that malformed JSON falls back to heuristic parsing."""
    from selfai.runner import Runner

    test_dir = tempfile.mkdtemp()
    try:
        repo_path = Path(test_dir) / 'test_repo'
        repo_path.mkdir()
        init_git_repo(repo_path)
        runner = Runner(repo_path)

        malformed1 = '```json\n{"test_passed": true\n```'
        result1 = runner._parse_test_result(malformed1)
        print(f"✓ Malformed JSON with pass indicator: {result1}")

        malformed2 = '```json\n{invalid json}\n```'
        result2 = runner._parse_test_result(malformed2)
        print(f"✓ Invalid JSON handled: {result2}")

        empty_json = '```json\n\n```'
        result3 = runner._parse_test_result(empty_json)
        if not result3:
            print("✓ Empty JSON block correctly marked as failed")
        else:
            print("✗ Empty JSON should fail")
            return False

        return True
    except Exception as e:
        print(f"✗ Test failed: {e}")
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_parse_result_string_boolean_values():
    """Test that string boolean values are converted correctly."""
    from selfai.runner import Runner

    test_dir = tempfile.mkdtemp()
    try:
        repo_path = Path(test_dir) / 'test_repo'
        repo_path.mkdir()
        init_git_repo(repo_path)
        runner = Runner(repo_path)

        string_true = '```json\n{"test_passed": "true"}\n```'
        if runner._parse_test_result(string_true):
            print("✓ String 'true' converted to boolean True")
        else:
            print("✗ String 'true' should convert to True")
            return False

        string_false = '```json\n{"test_passed": "false"}\n```'
        if not runner._parse_test_result(string_false):
            print("✓ String 'false' converted to boolean False")
        else:
            print("✗ String 'false' should convert to False")
            return False

        string_yes = '```json\n{"test_passed": "yes"}\n```'
        if runner._parse_test_result(string_yes):
            print("✓ String 'yes' converted to boolean True")
        else:
            print("✗ String 'yes' should convert to True")
            return False

        return True
    except Exception as e:
        print(f"✗ Test failed: {e}")
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_parse_result_ambiguous_output():
    """Test heuristic parsing with mixed indicators."""
    from selfai.runner import Runner

    test_dir = tempfile.mkdtemp()
    try:
        repo_path = Path(test_dir) / 'test_repo'
        repo_path.mkdir()
        init_git_repo(repo_path)
        runner = Runner(repo_path)

        more_pass = "Test passed successfully. One minor error was found but working overall. Complete and verified."
        if runner._parse_test_result(more_pass):
            print("✓ More pass indicators correctly identified as pass")
        else:
            print("✗ Should pass when pass indicators dominate")
            return False

        more_fail = "Test failed. Error occurred. Exception raised. Not working. One success noted."
        if not runner._parse_test_result(more_fail):
            print("✓ More fail indicators correctly identified as fail")
        else:
            print("✗ Should fail when fail indicators dominate")
            return False

        return True
    except Exception as e:
        print(f"✗ Test failed: {e}")
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_parse_result_unicode_handling():
    """Test that unicode and special characters are handled."""
    from selfai.runner import Runner

    test_dir = tempfile.mkdtemp()
    try:
        repo_path = Path(test_dir) / 'test_repo'
        repo_path.mkdir()
        init_git_repo(repo_path)
        runner = Runner(repo_path)

        unicode_output = '```json\n{"test_passed": true, "message": "Tests passed ✓ 成功"}\n```'
        if runner._parse_test_result(unicode_output):
            print("✓ Unicode characters handled correctly")
        else:
            print("✗ Unicode should not break parsing")
            return False

        emoji_output = 'All tests passed ✓ 🎉 No errors found 😊'
        result = runner._parse_test_result(emoji_output)
        print(f"✓ Emoji characters handled: {result}")

        return True
    except Exception as e:
        print(f"✗ Test failed: {e}")
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_retry_limit_enforcement():
    """Test that retry limit is enforced correctly."""
    from selfai.database import Database, MAX_TEST_RETRIES

    test_dir = tempfile.mkdtemp()
    try:
        db_path = Path(test_dir) / 'test.db'
        db = Database(db_path)

        imp_id = db.add(title="Test Retry Limit", description="Test", category="test", priority=50)

        for i in range(MAX_TEST_RETRIES):
            result = db.mark_test_failed(imp_id, level=1, test_output=f"Failure #{i+1}")
            if not result:
                print(f"✗ mark_test_failed failed on attempt {i+1}")
                return False

        improvement = db.get_by_id(imp_id)
        if improvement['retry_count'] == MAX_TEST_RETRIES:
            print(f"✓ Retry count correctly at max ({MAX_TEST_RETRIES})")
        else:
            print(f"✗ Retry count is {improvement['retry_count']}, expected {MAX_TEST_RETRIES}")
            return False

        if improvement['status'] == 'failed':
            print("✓ Status correctly set to 'failed' at retry limit")
        else:
            print(f"✗ Status is '{improvement['status']}', expected 'failed'")
            return False

        if improvement['error'] == 'Max test retries reached':
            print("✓ Error message correctly set")
        else:
            print(f"✗ Error is '{improvement['error']}', expected 'Max test retries reached'")
            return False

        return True
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_can_test_feature_validations():
    """Test various scenarios for can_test_feature."""
    from selfai.database import Database

    test_dir = tempfile.mkdtemp()
    try:
        db_path = Path(test_dir) / 'test.db'
        db = Database(db_path)

        can_test, reason = db.can_test_feature(99999)
        if not can_test and 'not found' in reason.lower():
            print("✓ Non-existent feature correctly rejected")
        else:
            print("✗ Should reject non-existent feature")
            return False

        imp_id = db.add(title="Test Feature", description="Test", category="test", priority=50)
        db.mark_in_progress(imp_id)

        can_test, reason = db.can_test_feature(imp_id)
        if can_test:
            print("✓ In-progress feature correctly allowed")
        else:
            print(f"✗ Should allow testing in-progress feature, got: {reason}")
            return False

        db.mark_level_completed(imp_id, 1, "Output")
        db.mark_test_passed(imp_id, 1, "Passed")

        can_test, reason = db.can_test_feature(imp_id)
        if not can_test:
            print(f"✓ Completed feature correctly rejected: {reason}")
        else:
            print(f"✗ Should reject completed feature")
            return False

        imp_id2 = db.add(title="Max Retries", description="Test", category="test", priority=50)
        db.mark_in_progress(imp_id2)
        db.mark_level_completed(imp_id2, 1, "Output")
        for _ in range(3):
            db.mark_test_failed(imp_id2, level=1, test_output="Failed")

        can_test, reason = db.can_test_feature(imp_id2)
        if not can_test and ('retry' in reason.lower() or 'failed' in reason.lower()):
            print(f"✓ Max-retry feature correctly rejected: {reason}")
        else:
            print(f"✗ Should reject feature at retry limit, got: {reason}")
            return False

        return True
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_test_output_sanitization():
    """Test that test output is sanitized before storage."""
    from selfai.database import Database

    test_dir = tempfile.mkdtemp()
    try:
        db_path = Path(test_dir) / 'test.db'
        db = Database(db_path)

        imp_id = db.add(title="Test Sanitization", description="Test", category="test", priority=50)

        very_long_output = "x" * 15000
        db.mark_test_failed(imp_id, level=1, test_output=very_long_output)

        improvement = db.get_by_id(imp_id)
        stored_output = improvement['mvp_test_output']

        if len(stored_output) <= 10100:
            print(f"✓ Long output truncated correctly ({len(stored_output)} chars)")
        else:
            print(f"✗ Output not truncated: {len(stored_output)} chars")
            return False

        if "truncated" in stored_output.lower():
            print("✓ Truncation indicator added")
        else:
            print("✗ Should indicate truncation")
            return False

        empty_output = ""
        imp_id2 = db.add(title="Test Empty Output", description="Test", category="test", priority=50)
        db.mark_test_failed(imp_id2, level=1, test_output=empty_output)

        improvement2 = db.get_by_id(imp_id2)
        stored = improvement2['mvp_test_output']
        if stored == "":
            print("✓ Empty output handled correctly")
        else:
            print(f"✗ Empty output should remain empty: '{stored}'")
            return False

        return True
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_validate_test_environment():
    """Test environment validation before testing."""
    from selfai.runner import Runner

    test_dir = tempfile.mkdtemp()
    try:
        repo_path = Path(test_dir) / 'test_repo'
        repo_path.mkdir()
        init_git_repo(repo_path)
        runner = Runner(repo_path)

        fake_improvement = {'id': 99999}
        valid, error = runner._validate_test_environment(fake_improvement)

        if not valid and 'worktree not found' in error.lower():
            print("✓ Missing worktree correctly detected")
        else:
            print("✗ Should detect missing worktree")
            return False

        worktrees_path = repo_path / '.selfai_data' / 'worktrees'
        worktrees_path.mkdir(parents=True, exist_ok=True)
        fake_worktree = worktrees_path / 'wt-99999'
        fake_worktree.mkdir()

        valid, error = runner._validate_test_environment(fake_improvement)
        if not valid and 'missing selfai module' in error.lower():
            print("✓ Missing selfai module correctly detected")
        else:
            print("✗ Should detect missing selfai module")
            return False

        (fake_worktree / 'selfai').mkdir()

        valid, error = runner._validate_test_environment(fake_improvement)
        if not valid and 'required file missing' in error.lower():
            print("✓ Missing required files correctly detected")
        else:
            print(f"✗ Should detect missing required files: {error}")
            return False

        (fake_worktree / 'selfai' / '__init__.py').touch()
        (fake_worktree / 'selfai' / 'runner.py').touch()
        (fake_worktree / 'selfai' / 'database.py').touch()

        valid, error = runner._validate_test_environment(fake_improvement)
        if valid:
            print("✓ Valid environment correctly accepted")
        else:
            print(f"✗ Should accept valid environment: {error}")
            return False

        return True
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_parse_result_non_dict_json():
    """Test that non-dict JSON is handled gracefully."""
    from selfai.runner import Runner

    test_dir = tempfile.mkdtemp()
    try:
        repo_path = Path(test_dir) / 'test_repo'
        repo_path.mkdir()
        init_git_repo(repo_path)
        runner = Runner(repo_path)

        json_array = '```json\n["test", "array"]\n```'
        result = runner._parse_test_result(json_array)
        print(f"✓ JSON array handled gracefully: {result}")

        json_string = '```json\n"just a string"\n```'
        result2 = runner._parse_test_result(json_string)
        print(f"✓ JSON string handled gracefully: {result2}")

        json_number = '```json\n123\n```'
        result3 = runner._parse_test_result(json_number)
        print(f"✓ JSON number handled gracefully: {result3}")

        return True
    except Exception as e:
        print(f"✗ Test failed: {e}")
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


if __name__ == '__main__':
    print("Testing Autonomous Testing and Validation - Enhanced Level\n")
    print("=" * 60)

    results = []

    print("\n1. Testing empty output handling...")
    results.append(test_parse_result_empty_output())

    print("\n2. Testing malformed JSON handling...")
    results.append(test_parse_result_malformed_json())

    print("\n3. Testing string boolean conversion...")
    results.append(test_parse_result_string_boolean_values())

    print("\n4. Testing ambiguous output heuristics...")
    results.append(test_parse_result_ambiguous_output())

    print("\n5. Testing unicode and emoji handling...")
    results.append(test_parse_result_unicode_handling())

    print("\n6. Testing retry limit enforcement...")
    results.append(test_retry_limit_enforcement())

    print("\n7. Testing can_test_feature validations...")
    results.append(test_can_test_feature_validations())

    print("\n8. Testing output sanitization...")
    results.append(test_test_output_sanitization())

    print("\n9. Testing environment validation...")
    results.append(test_validate_test_environment())

    print("\n10. Testing non-dict JSON handling...")
    results.append(test_parse_result_non_dict_json())

    print("\n" + "=" * 60)
    print(f"\nResults: {sum(results)}/{len(results)} tests passed")

    if all(results):
        print("\n✓ All Enhanced tests passed!")
        exit(0)
    else:
        print("\n✗ Some tests failed")
        exit(1)
