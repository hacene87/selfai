"""Enhanced level tests for Self-Diagnosis and Log Analysis.

Tests input validation, edge cases, and error handling.
"""
import tempfile
import shutil
import json
from pathlib import Path
from selfai.runner import LogAnalyzer, ValidationError


def test_get_recent_logs_invalid_type():
    """Test that get_recent_logs validates input type."""
    test_dir = tempfile.mkdtemp()
    try:
        logs_path = Path(test_dir) / 'logs'
        logs_path.mkdir()
        analyzer = LogAnalyzer(logs_path, 'claude')

        try:
            analyzer.get_recent_logs("100")
            print("✗ Should have raised ValidationError for string input")
            return False
        except ValidationError as e:
            if "must be an integer" in str(e):
                print("✓ Correctly validates lines parameter type")
                return True
            else:
                print(f"✗ Wrong error message: {e}")
                return False
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_get_recent_logs_negative():
    """Test that get_recent_logs rejects negative values."""
    test_dir = tempfile.mkdtemp()
    try:
        logs_path = Path(test_dir) / 'logs'
        logs_path.mkdir()
        analyzer = LogAnalyzer(logs_path, 'claude')

        try:
            analyzer.get_recent_logs(-10)
            print("✗ Should have raised ValidationError for negative input")
            return False
        except ValidationError as e:
            if "must be positive" in str(e):
                print("✓ Correctly rejects negative lines")
                return True
            else:
                print(f"✗ Wrong error message: {e}")
                return False
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_get_recent_logs_too_large():
    """Test that get_recent_logs rejects excessively large values."""
    test_dir = tempfile.mkdtemp()
    try:
        logs_path = Path(test_dir) / 'logs'
        logs_path.mkdir()
        analyzer = LogAnalyzer(logs_path, 'claude')

        try:
            analyzer.get_recent_logs(200000)
            print("✗ Should have raised ValidationError for too large input")
            return False
        except ValidationError as e:
            if "too large" in str(e):
                print("✓ Correctly rejects excessively large values")
                return True
            else:
                print(f"✗ Wrong error message: {e}")
                return False
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_get_recent_logs_empty_file():
    """Test handling of empty log file."""
    test_dir = tempfile.mkdtemp()
    try:
        logs_path = Path(test_dir) / 'logs'
        logs_path.mkdir()
        log_file = logs_path / 'runner.log'
        log_file.write_text('')

        analyzer = LogAnalyzer(logs_path, 'claude')
        result = analyzer.get_recent_logs(10)

        if result == "":
            print("✓ Handles empty log file gracefully")
            return True
        else:
            print(f"✗ Should return empty string for empty file, got: {result}")
            return False
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_diagnose_and_fix_invalid_issue_type():
    """Test that diagnose_and_fix validates issue type."""
    test_dir = tempfile.mkdtemp()
    try:
        logs_path = Path(test_dir) / 'logs'
        logs_path.mkdir()
        repo_path = Path(test_dir) / 'repo'
        repo_path.mkdir()

        analyzer = LogAnalyzer(logs_path, 'claude')

        try:
            analyzer.diagnose_and_fix("not a dict", repo_path)
            print("✗ Should have raised ValidationError for non-dict issue")
            return False
        except ValidationError as e:
            if "must be a dict" in str(e):
                print("✓ Correctly validates issue type")
                return True
            else:
                print(f"✗ Wrong error message: {e}")
                return False
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_diagnose_and_fix_empty_issue():
    """Test that diagnose_and_fix rejects empty issue dict."""
    test_dir = tempfile.mkdtemp()
    try:
        logs_path = Path(test_dir) / 'logs'
        logs_path.mkdir()
        repo_path = Path(test_dir) / 'repo'
        repo_path.mkdir()

        analyzer = LogAnalyzer(logs_path, 'claude')

        try:
            analyzer.diagnose_and_fix({}, repo_path)
            print("✗ Should have raised ValidationError for empty dict")
            return False
        except ValidationError as e:
            if "cannot be empty" in str(e):
                print("✓ Correctly rejects empty issue dict")
                return True
            else:
                print(f"✗ Wrong error message: {e}")
                return False
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_diagnose_and_fix_missing_keys():
    """Test that diagnose_and_fix validates issue has required keys."""
    test_dir = tempfile.mkdtemp()
    try:
        logs_path = Path(test_dir) / 'logs'
        logs_path.mkdir()
        repo_path = Path(test_dir) / 'repo'
        repo_path.mkdir()

        analyzer = LogAnalyzer(logs_path, 'claude')

        try:
            analyzer.diagnose_and_fix({'type': 'error'}, repo_path)
            print("✗ Should have raised ValidationError for missing 'detail' key")
            return False
        except ValidationError as e:
            if "must have 'type' and 'detail' keys" in str(e):
                print("✓ Correctly validates required keys")
                return True
            else:
                print(f"✗ Wrong error message: {e}")
                return False
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_diagnose_and_fix_invalid_repo_type():
    """Test that diagnose_and_fix validates repo_path type."""
    test_dir = tempfile.mkdtemp()
    try:
        logs_path = Path(test_dir) / 'logs'
        logs_path.mkdir()

        analyzer = LogAnalyzer(logs_path, 'claude')
        issue = {'type': 'error', 'detail': 'test'}

        try:
            analyzer.diagnose_and_fix(issue, "/not/a/path/object")
            print("✗ Should have raised ValidationError for non-Path repo_path")
            return False
        except ValidationError as e:
            if "must be a Path object" in str(e):
                print("✓ Correctly validates repo_path type")
                return True
            else:
                print(f"✗ Wrong error message: {e}")
                return False
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_diagnose_and_fix_nonexistent_repo():
    """Test that diagnose_and_fix validates repo exists."""
    test_dir = tempfile.mkdtemp()
    try:
        logs_path = Path(test_dir) / 'logs'
        logs_path.mkdir()

        analyzer = LogAnalyzer(logs_path, 'claude')
        issue = {'type': 'error', 'detail': 'test'}
        nonexistent_path = Path(test_dir) / 'nonexistent'

        try:
            analyzer.diagnose_and_fix(issue, nonexistent_path)
            print("✗ Should have raised ValidationError for nonexistent repo")
            return False
        except ValidationError as e:
            if "does not exist" in str(e):
                print("✓ Correctly validates repo exists")
                return True
            else:
                print(f"✗ Wrong error message: {e}")
                return False
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_diagnose_and_fix_repo_not_directory():
    """Test that diagnose_and_fix validates repo is directory."""
    test_dir = tempfile.mkdtemp()
    try:
        logs_path = Path(test_dir) / 'logs'
        logs_path.mkdir()
        file_path = Path(test_dir) / 'file.txt'
        file_path.write_text('test')

        analyzer = LogAnalyzer(logs_path, 'claude')
        issue = {'type': 'error', 'detail': 'test'}

        try:
            analyzer.diagnose_and_fix(issue, file_path)
            print("✗ Should have raised ValidationError for file instead of directory")
            return False
        except ValidationError as e:
            if "not a directory" in str(e):
                print("✓ Correctly validates repo is directory")
                return True
            else:
                print(f"✗ Wrong error message: {e}")
                return False
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_think_about_improvements_invalid_stats_type():
    """Test that think_about_improvements validates stats type."""
    test_dir = tempfile.mkdtemp()
    try:
        logs_path = Path(test_dir) / 'logs'
        logs_path.mkdir()
        repo_path = Path(test_dir) / 'repo'
        repo_path.mkdir()

        analyzer = LogAnalyzer(logs_path, 'claude')

        try:
            analyzer.think_about_improvements("not a dict", repo_path)
            print("✗ Should have raised ValidationError for non-dict stats")
            return False
        except ValidationError as e:
            if "must be a dict" in str(e):
                print("✓ Correctly validates stats type")
                return True
            else:
                print(f"✗ Wrong error message: {e}")
                return False
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_think_about_improvements_none_stats():
    """Test that think_about_improvements rejects None stats."""
    test_dir = tempfile.mkdtemp()
    try:
        logs_path = Path(test_dir) / 'logs'
        logs_path.mkdir()
        repo_path = Path(test_dir) / 'repo'
        repo_path.mkdir()

        analyzer = LogAnalyzer(logs_path, 'claude')

        try:
            analyzer.think_about_improvements(None, repo_path)
            print("✗ Should have raised ValidationError for None stats")
            return False
        except ValidationError as e:
            if "must be a dict" in str(e):
                print("✓ Correctly rejects None stats")
                return True
            else:
                print(f"✗ Wrong error message: {e}")
                return False
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_save_issues_invalid_type():
    """Test that save_issues validates input type."""
    test_dir = tempfile.mkdtemp()
    try:
        logs_path = Path(test_dir) / 'logs'
        logs_path.mkdir()
        analyzer = LogAnalyzer(logs_path, 'claude')

        try:
            analyzer.save_issues("not a list")
            print("✗ Should have raised ValidationError for non-list input")
            return False
        except ValidationError as e:
            if "must be a list" in str(e):
                print("✓ Correctly validates issues type")
                return True
            else:
                print(f"✗ Wrong error message: {e}")
                return False
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_save_issues_none_input():
    """Test that save_issues rejects None input."""
    test_dir = tempfile.mkdtemp()
    try:
        logs_path = Path(test_dir) / 'logs'
        logs_path.mkdir()
        analyzer = LogAnalyzer(logs_path, 'claude')

        try:
            analyzer.save_issues(None)
            print("✗ Should have raised ValidationError for None input")
            return False
        except ValidationError as e:
            if "must be a list" in str(e):
                print("✓ Correctly rejects None input")
                return True
            else:
                print(f"✗ Wrong error message: {e}")
                return False
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_save_issues_corrupted_existing_file():
    """Test that save_issues handles corrupted existing file."""
    test_dir = tempfile.mkdtemp()
    try:
        logs_path = Path(test_dir) / 'logs'
        logs_path.mkdir()
        issues_file = logs_path / 'issues.json'
        issues_file.write_text('{"corrupted json')

        analyzer = LogAnalyzer(logs_path, 'claude')
        analyzer.save_issues([{'type': 'error', 'detail': 'test'}])

        content = json.loads(issues_file.read_text())
        if len(content) == 1 and content[0]['type'] == 'error':
            print("✓ Handles corrupted existing file gracefully")
            return True
        else:
            print(f"✗ Unexpected content after corruption recovery: {content}")
            return False
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_save_improvements_invalid_type():
    """Test that save_improvements validates input type."""
    test_dir = tempfile.mkdtemp()
    try:
        logs_path = Path(test_dir) / 'logs'
        logs_path.mkdir()
        analyzer = LogAnalyzer(logs_path, 'claude')

        try:
            analyzer.save_improvements({'not': 'a list'})
            print("✗ Should have raised ValidationError for non-list input")
            return False
        except ValidationError as e:
            if "must be a list" in str(e):
                print("✓ Correctly validates improvements type")
                return True
            else:
                print(f"✗ Wrong error message: {e}")
                return False
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_save_improvements_corrupted_existing_file():
    """Test that save_improvements handles corrupted existing file."""
    test_dir = tempfile.mkdtemp()
    try:
        logs_path = Path(test_dir) / 'logs'
        logs_path.mkdir()
        improvements_file = logs_path / 'self_improvements.json'
        improvements_file.write_text('[invalid json structure')

        analyzer = LogAnalyzer(logs_path, 'claude')
        analyzer.save_improvements([{'title': 'test', 'priority': 50}])

        content = json.loads(improvements_file.read_text())
        if len(content) == 1 and content[0]['title'] == 'test':
            print("✓ Handles corrupted improvements file gracefully")
            return True
        else:
            print(f"✗ Unexpected content after corruption recovery: {content}")
            return False
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def run_all_tests():
    """Run all enhanced tests."""
    tests = [
        test_get_recent_logs_invalid_type,
        test_get_recent_logs_negative,
        test_get_recent_logs_too_large,
        test_get_recent_logs_empty_file,
        test_diagnose_and_fix_invalid_issue_type,
        test_diagnose_and_fix_empty_issue,
        test_diagnose_and_fix_missing_keys,
        test_diagnose_and_fix_invalid_repo_type,
        test_diagnose_and_fix_nonexistent_repo,
        test_diagnose_and_fix_repo_not_directory,
        test_think_about_improvements_invalid_stats_type,
        test_think_about_improvements_none_stats,
        test_save_issues_invalid_type,
        test_save_issues_none_input,
        test_save_issues_corrupted_existing_file,
        test_save_improvements_invalid_type,
        test_save_improvements_corrupted_existing_file,
    ]

    print("\n=== Running Enhanced Log Analyzer Tests ===\n")
    passed = 0
    failed = 0

    for test in tests:
        if test():
            passed += 1
        else:
            failed += 1

    print(f"\n=== Results: {passed} passed, {failed} failed ===\n")
    return failed == 0


if __name__ == '__main__':
    import sys
    success = run_all_tests()
    sys.exit(0 if success else 1)
