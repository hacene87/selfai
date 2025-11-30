"""SelfAI Runner - Planning-First Workflow.

New workflow:
1. Pending tasks get detailed plans generated (with internet research)
2. Plans go to 'plan_review' status for user approval
3. Approved plans get executed
4. Features are tested (max 3 attempts)
5. After 3 test failures -> cancelled (needs user feedback to re-enable)
"""
import os
import sys
import subprocess
import time
import json
import logging
import shutil
import fcntl
import re
import psutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher

from .database import Database, MAX_PARALLEL_TASKS, MAX_TEST_ATTEMPTS
from .test_environment import TestEnvironmentManager
from .discovery import DiscoveryEngine, DiscoveryCategory
from .monitoring import SelfHealingMonitor
from .worktree_manager import WorktreeManager
from .exceptions import ValidationError, GitOperationError

logger = logging.getLogger('selfai')


def _extract_json_from_output(output: str) -> Optional[dict | list]:
    """
    Safely extract JSON from Claude CLI output.

    Handles cases where:
    - Output is empty
    - Output contains markdown code blocks
    - Output has text before/after JSON
    - JSON is malformed
    """
    if not output or not output.strip():
        logger.warning("Empty output from Claude CLI")
        return None

    text = output.strip()

    # Try to extract from markdown code blocks first
    import re
    json_block = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if json_block:
        text = json_block.group(1).strip()

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON object or array in the text
    for pattern in [r'(\{[\s\S]*\})', r'(\[[\s\S]*\])']:
        match = re.search(pattern, text)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                continue

    logger.warning(f"Could not extract JSON from output (first 200 chars): {text[:200]}")
    return None

# Claude CLI command
CLAUDE_CMD = os.environ.get('CLAUDE_CMD', 'claude')


def _create_subprocess_error_response(result: subprocess.CompletedProcess, context: str, timed_out: bool = False) -> dict:
    """Create structured error response for failed Claude CLI calls.

    Args:
        result: CompletedProcess from subprocess.run
        context: Description of operation (e.g., 'Diagnosis', 'Discovery')
        timed_out: Whether the call timed out

    Returns:
        Dict with error diagnostics
    """
    return {
        'error': True,
        'context': context,
        'returncode': result.returncode,
        'stderr_snippet': result.stderr[:500] if result.stderr else '',
        'stdout_length': len(result.stdout) if result.stdout else 0,
        'timed_out': timed_out,
        'timestamp': datetime.now().isoformat()
    }


def _log_subprocess_diagnostics(result: subprocess.CompletedProcess, context: str):
    """Log diagnostic information from subprocess call.

    Args:
        result: CompletedProcess object from subprocess.run()
        context: Description of what the subprocess was doing (e.g., 'Diagnosis')
    """
    logger.debug(f"[{context}] subprocess returncode: {result.returncode}")
    logger.debug(f"[{context}] stdout length: {len(result.stdout) if result.stdout else 0} chars")

    if result.stderr:
        logger.warning(f"[{context}] stderr: {result.stderr[:500]}")

    if not result.stdout or not result.stdout.strip():
        logger.warning(f"[{context}] EMPTY stdout detected - Claude CLI returned no output")


def retry_with_backoff(max_attempts: int = 3, base_delay: int = 2):
    """Decorator for retrying subprocess calls with exponential backoff.

    Args:
        max_attempts: Maximum number of retry attempts (default: 3)
        base_delay: Base delay in seconds, doubles with each retry (default: 2)
                   Results in delays of 2s, 4s, 8s for 3 attempts

    Returns:
        Decorator function that wraps subprocess calls with retry logic
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                    last_exception = e
                    if attempt == max_attempts:
                        logger.error(f"Max retries ({max_attempts}) reached for {func.__name__}: {e}")
                        raise
                    delay = base_delay * (2 ** (attempt - 1))
                    logger.warning(f"Attempt {attempt}/{max_attempts} failed for {func.__name__}, retrying in {delay}s: {e}")
                    time.sleep(delay)
            if last_exception:
                raise last_exception
        return wrapper
    return decorator


# Level guidance for the 3-level complexity system
LEVEL_GUIDANCE = {
    1: {  # MVP Level
        'name': 'MVP',
        'description': 'Simple, working implementation with core functionality only',
        'scope': [
            'Basic functionality that addresses the core need',
            'No edge case handling',
            'Minimal error handling',
            'No optimization',
            'Can use hardcoded values where appropriate'
        ],
        'test_criteria': [
            'No syntax errors',
            'Imports work correctly',
            'Basic happy path works',
            'Feature can be invoked without crashing'
        ],
        'prompt_suffix': 'IMPORTANT: This is MVP level. Keep it simple. Only implement the core functionality. Do NOT add error handling, edge cases, or optimizations.'
    },
    2: {  # Enhanced Level
        'name': 'Enhanced',
        'description': 'Robust implementation with edge cases and proper error handling',
        'scope': [
            'All edge cases handled',
            'Comprehensive error handling with meaningful messages',
            'Input validation',
            'Proper logging',
            'Configuration options where needed'
        ],
        'test_criteria': [
            'All MVP tests still pass',
            'Edge cases are handled gracefully',
            'Invalid inputs return helpful errors',
            'No unhandled exceptions',
            'Logging works correctly'
        ],
        'prompt_suffix': 'IMPORTANT: This is Enhanced level. Build on the MVP to add proper error handling, edge case coverage, and input validation.'
    },
    3: {  # Advanced Level
        'name': 'Advanced',
        'description': 'Production-ready with performance, monitoring, and documentation',
        'scope': [
            'Performance optimized',
            'Comprehensive documentation',
            'Monitoring/metrics hooks',
            'Security hardened',
            'Backwards compatible API'
        ],
        'test_criteria': [
            'All Enhanced tests still pass',
            'Performance benchmarks met',
            'Documentation complete and accurate',
            'Security review passed',
            'No memory leaks or resource issues'
        ],
        'prompt_suffix': 'IMPORTANT: This is Advanced level. Make this production-ready with performance optimization, documentation, and security hardening.'
    }
}


class LogAnalyzer:
    """Analyzes system logs for errors, patterns, and performance issues."""

    def __init__(self, data_dir: Path, claude_cmd: str):
        self.data_dir = data_dir
        self.log_dir = data_dir / 'logs'
        self.log_file = self.log_dir / 'runner.log'
        self.claude_cmd = claude_cmd

        # Pattern library
        self.error_patterns = [
            (r'ERROR[:\s]+(.+)', 'error'),
            (r'Exception[:\s]+(.+)', 'exception'),
            (r'Failed[:\s]+(.+)', 'failure'),
            (r'Timeout[:\s]+(.+)', 'timeout'),
            (r'CONFLICT[:\s]+(.+)', 'conflict'),
        ]

        # Learning database
        self.issues_file = self.data_dir / 'issues.json'
        self.improvements_file = self.data_dir / 'improvements.json'
        self.patterns_db = self.data_dir / 'patterns.json'

    def analyze_logs(self, max_lines: int = 10000) -> Dict:
        """Analyze recent logs for errors and patterns."""
        if not self.log_file.exists():
            return {'log_lines': 0, 'issues': [], 'issues_found': 0}

        issues = []
        log_text = self.log_file.read_text()
        lines = log_text.split('\n')[-max_lines:]

        for line in lines:
            timestamp = self._extract_timestamp(line)
            for pattern, issue_type in self.error_patterns:
                match = re.search(pattern, line)
                if match:
                    issues.append({
                        'type': issue_type,
                        'detail': match.group(1).strip() if match.groups() else line,
                        'timestamp': timestamp or datetime.now().isoformat(),
                        'full_line': line
                    })
                    break

        return {
            'log_lines': len(lines),
            'issues': issues,
            'issues_found': len(issues)
        }

    def diagnose_and_fix(self, issue: Dict, repo_path: Path) -> Dict:
        """Diagnose an issue and attempt automated fix."""
        # Input validation
        if not isinstance(issue, dict):
            raise ValidationError('issue must be a dict')
        if not issue:
            raise ValidationError('issue dict cannot be empty')
        if 'type' not in issue or 'detail' not in issue:
            raise ValidationError("issue must have 'type' and 'detail' keys")

        # Check pattern library first
        known_fix = self._check_pattern_library(issue)
        if known_fix:
            logger.info(f"Found known fix for {issue['type']}: {known_fix['diagnosis']}")
            return known_fix

        # Use Claude for diagnosis
        prompt = f"""Diagnose and fix this issue:

Type: {issue['type']}
Detail: {issue['detail']}
Context: {issue.get('full_line', 'N/A')}

Analyze the error and provide:
1. Root cause diagnosis
2. Specific fix (code changes if needed)
3. Prevention strategy

Format response as JSON:
{{
  "diagnosis": "...",
  "fix_description": "...",
  "fix_commands": ["cmd1", "cmd2"],
  "confidence": 0.85,
  "prevention": "..."
}}"""

        try:
            result = subprocess.run(
                [self.claude_cmd, '-p', prompt, '--allowedTools', 'Read,Grep,Glob'],
                capture_output=True, text=True, timeout=180, cwd=str(repo_path)
            )

            if result.returncode == 0:
                diagnosis = _extract_json_from_output(result.stdout)
                if diagnosis and isinstance(diagnosis, dict):
                    self._learn_from_fix(issue, diagnosis)
                    return diagnosis
                else:
                    logger.warning(f"Invalid diagnosis response for {issue['type']}")
                    # Return structured error instead of falling through
                    error_response = _create_subprocess_error_response(result, 'Diagnosis')
                    self._store_error_pattern(error_response)
                    return error_response
            else:
                # Non-zero returncode
                error_response = _create_subprocess_error_response(result, 'Diagnosis')
                self._store_error_pattern(error_response)
                return error_response

        except subprocess.TimeoutExpired as e:
            logger.error(f"Diagnosis timed out for {issue['type']}")
            # Create error response from timeout exception
            result = subprocess.CompletedProcess(args=e.cmd, returncode=-1, stdout=e.stdout or '', stderr=e.stderr or '')
            error_response = _create_subprocess_error_response(result, 'Diagnosis', timed_out=True)
            self._store_error_pattern(error_response)
            return error_response
        except Exception as e:
            logger.error(f"Diagnosis failed: {e}")
            # Generic error response
            return {'error': True, 'context': 'Diagnosis', 'message': str(e), 'timestamp': datetime.now().isoformat()}

    def think_about_improvements(self, stats: Dict, repo_path: Path) -> List[Dict]:
        """Analyze patterns and suggest proactive improvements."""
        # Validation
        if stats is None:
            raise ValidationError('stats cannot be None')
        if not isinstance(stats, dict):
            raise ValidationError('stats must be a dict')
        if not isinstance(repo_path, Path):
            raise ValidationError('repo_path must be a Path object')
        if not repo_path.exists():
            raise ValidationError('Repository path does not exist')

        # Load historical patterns
        patterns = self._load_patterns()

        # Analyze trends
        prompt = f"""Analyze system health and suggest improvements:

Stats: {json.dumps(stats)}
Recent patterns: {json.dumps(patterns[-10:])}

Suggest improvements for:
1. Preventing recurring errors
2. Performance optimization
3. Code quality enhancements
4. System reliability

Return JSON array:
[
  {{
    "title": "Short improvement title",
    "description": "Detailed description",
    "category": "reliability|performance|quality",
    "priority": 1-100,
    "reasoning": "Why this helps"
  }}
]"""

        try:
            result = subprocess.run(
                [self.claude_cmd, '-p', prompt, '--allowedTools', 'Read,Grep,WebSearch'],
                capture_output=True, text=True, timeout=240, cwd=str(repo_path)
            )

            if result.returncode == 0:
                improvements = _extract_json_from_output(result.stdout)
                if improvements and isinstance(improvements, list):
                    self.save_improvements(improvements)
                    return improvements
                else:
                    logger.warning("Invalid improvement analysis response (expected list)")
                    error_response = _create_subprocess_error_response(result, 'ImprovementAnalysis')
                    self._store_error_pattern(error_response)
                    return []  # Return empty list but log error
            else:
                error_response = _create_subprocess_error_response(result, 'ImprovementAnalysis')
                self._store_error_pattern(error_response)
                return []

        except subprocess.TimeoutExpired as e:
            logger.error("Improvement analysis timed out")
            result = subprocess.CompletedProcess(args=e.cmd, returncode=-1, stdout=e.stdout or '', stderr=e.stderr or '')
            error_response = _create_subprocess_error_response(result, 'ImprovementAnalysis', timed_out=True)
            self._store_error_pattern(error_response)
            return []
        except Exception as e:
            logger.error(f"Improvement analysis failed: {e}")
            return []

    def _learn_from_fix(self, issue: Dict, diagnosis: Dict):
        """Store successful fix in pattern library for future reference."""
        patterns = self._load_patterns()

        pattern_entry = {
            'issue_type': issue['type'],
            'pattern': issue['detail'][:200],
            'diagnosis': diagnosis.get('diagnosis', ''),
            'fix': diagnosis.get('fix_description', ''),
            'confidence': diagnosis.get('confidence', 0.5),
            'success_count': 1,
            'timestamp': datetime.now().isoformat()
        }

        # Check if similar pattern exists
        similar = self._find_similar_pattern(patterns, issue)
        if similar:
            similar['success_count'] += 1
            similar['confidence'] = min(0.99, similar['confidence'] * 1.1)
            similar['last_seen'] = datetime.now().isoformat()
        else:
            patterns.append(pattern_entry)

        self._save_patterns(patterns)
        logger.info(f"Learned from fix: {issue['type']}")

    def _load_patterns(self) -> List[Dict]:
        """Load pattern library from disk."""
        if not self.patterns_db.exists():
            return []
        try:
            return json.loads(self.patterns_db.read_text())
        except json.JSONDecodeError:
            return []

    def _save_patterns(self, patterns: List[Dict]):
        """Save pattern library to disk."""
        self.patterns_db.write_text(json.dumps(patterns, indent=2))

    def _store_error_pattern(self, error_response: dict):
        """Store subprocess error in patterns.json for trend analysis.

        Args:
            error_response: Structured error dict from _create_subprocess_error_response
        """
        patterns = self._load_patterns()

        # Create pattern entry
        error_pattern = {
            'issue_type': 'subprocess_error',
            'pattern': f"{error_response['context']}: returncode={error_response['returncode']}, timed_out={error_response['timed_out']}",
            'diagnosis': error_response.get('stderr_snippet', 'No stderr'),
            'confidence': 0.5,
            'success_count': 1,
            'timestamp': error_response['timestamp'],
            'metadata': {
                'stdout_length': error_response['stdout_length'],
                'returncode': error_response['returncode'],
                'timed_out': error_response['timed_out']
            }
        }

        patterns.append(error_pattern)
        self._save_patterns(patterns)

    def _check_pattern_library(self, issue: Dict) -> Optional[Dict]:
        """Check if issue matches known pattern."""
        patterns = self._load_patterns()
        for pattern in patterns:
            if pattern['issue_type'] == issue['type']:
                if pattern['confidence'] > 0.7:
                    detail_sim = self._similarity(pattern['pattern'], issue['detail'])
                    if detail_sim > 0.8:
                        return pattern
        return None

    def _find_similar_pattern(self, patterns: List[Dict], issue: Dict) -> Optional[Dict]:
        """Find similar pattern in existing patterns."""
        for pattern in patterns:
            if pattern['issue_type'] == issue['type']:
                detail_sim = self._similarity(pattern['pattern'], issue['detail'])
                if detail_sim > 0.85:
                    return pattern
        return None

    def get_recent_logs(self, lines: int = 100) -> str:
        """Get recent log lines."""
        if not isinstance(lines, int):
            raise ValidationError('lines must be an integer')
        if lines <= 0:
            raise ValidationError('lines must be positive')
        if lines > 100000:
            raise ValidationError('lines too large (max 100000)')

        if not self.log_file.exists():
            return ''

        log_text = self.log_file.read_text()
        return '\n'.join(log_text.split('\n')[-lines:])

    def save_issues(self, issues: List[Dict]):
        """Save issues to file."""
        if issues is None:
            raise ValidationError('issues cannot be None')
        if not isinstance(issues, list):
            raise ValidationError('issues must be a list')

        self.issues_file.write_text(json.dumps(issues, indent=2))

    def save_improvements(self, improvements: List[Dict]):
        """Save improvements to file."""
        if improvements is None:
            raise ValidationError('improvements cannot be None')
        if not isinstance(improvements, list):
            raise ValidationError('improvements must be a list')

        self.improvements_file.write_text(json.dumps(improvements, indent=2))

    def _extract_timestamp(self, line: str) -> Optional[str]:
        """Extract timestamp from log line."""
        match = re.match(r'^(\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2})', line)
        return match.group(1) if match else None

    def _similarity(self, str1: str, str2: str) -> float:
        """Calculate string similarity (simple ratio)."""
        return SequenceMatcher(None, str1.lower(), str2.lower()).ratio()


class SelfAIRunner:
    """Main runner for the planning-first workflow."""

    def __init__(self, repo_path: Path):
        self.repo_path = repo_path
        self.data_dir = repo_path / '.selfai_data'
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Initialize database
        self.db = Database(self.data_dir / 'data' / 'improvements.db')

        # Lock file
        self.lock_file = self.data_dir / 'runner.lock'
        self.lock_fd = None

        # Initialize test environment manager for isolated testing
        self.test_env_manager = TestEnvironmentManager(
            repo_path,
            max_environments=MAX_PARALLEL_TASKS
        )

        # Initialize worktree manager for parallel execution
        self.worktree_manager = WorktreeManager(
            repo_path=self.repo_path,
            worktrees_dir=self.data_dir / 'worktrees'
        )

        # Setup logging
        self._setup_logging()

        # Initialize self-healing monitor
        self.monitor = SelfHealingMonitor(repo_path)

        # Initialize log analyzer
        self.log_analyzer = LogAnalyzer(self.data_dir, CLAUDE_CMD)

        # Cleanup orphaned worktrees on startup
        self.worktree_manager.prune_orphaned_worktrees()

    def _setup_logging(self):
        """Setup file logging."""
        log_dir = self.data_dir / 'logs'
        log_dir.mkdir(parents=True, exist_ok=True)

        handler = logging.FileHandler(log_dir / 'runner.log')
        handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(levelname)s - %(name)s - %(message)s'
        ))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

    def acquire_lock(self) -> bool:
        """Acquire exclusive lock with stale lock detection."""
        try:
            # Check if lock file exists
            if self.lock_file.exists():
                # Read existing PID
                try:
                    existing_pid = int(self.lock_file.read_text().strip())
                    if self._is_process_running(existing_pid):
                        logger.info(f"Another instance (PID {existing_pid}) is running")
                        return False
                    else:
                        logger.warning(f"Detected stale lock from PID {existing_pid}, cleaning up")
                        self.lock_file.unlink(missing_ok=True)
                except (ValueError, OSError) as e:
                    logger.warning(f"Invalid lock file, removing: {e}")
                    self.lock_file.unlink(missing_ok=True)

            # Acquire lock
            self.lock_fd = open(self.lock_file, 'w')
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.lock_fd.write(str(os.getpid()))
            self.lock_fd.flush()
            logger.info(f"Lock acquired by PID {os.getpid()}")
            return True
        except (IOError, OSError) as e:
            if self.lock_fd:
                self.lock_fd.close()
            logger.info(f"Failed to acquire lock: {e}")
            return False

    def _is_process_running(self, pid: int) -> bool:
        """Check if a process is actually running (not zombie/dead)."""
        try:
            process = psutil.Process(pid)
            # Check process exists and is not dead/zombie
            if psutil.pid_exists(pid) and process.status() not in (
                psutil.STATUS_DEAD, psutil.STATUS_ZOMBIE
            ):
                return True
            return False
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.Error):
            return False

    def release_lock(self):
        """Release lock."""
        if self.lock_fd:
            try:
                fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
                self.lock_fd.close()
                self.lock_file.unlink(missing_ok=True)
            except Exception:
                pass

    def _discover_existing_features(self, categories: List[str] = None) -> int:
        """Discover potential improvements in the codebase.

        Returns the number of new improvements discovered.
        """
        logger.info("Starting autonomous improvement discovery...")

        # Map string categories to enums
        if categories:
            cat_enums = [DiscoveryCategory(c) for c in categories]
        else:
            cat_enums = None

        engine = DiscoveryEngine(self.repo_path, self.db)
        discoveries = engine.discover_all(cat_enums)

        # Filter out already existing improvements
        new_discoveries = engine._filter_existing(discoveries)

        # Add to database
        added_count = 0
        for d in new_discoveries:
            try:
                self.db.add_discovered(
                    title=d.title,
                    description=d.description,
                    category=d.category.value,
                    priority=d.priority,
                    discovery_source=d.category.value,
                    metadata=d.metadata,
                    confidence=d.confidence
                )
                added_count += 1
                logger.info(f"Discovered: {d.title} (priority: {d.priority})")
            except Exception as e:
                logger.warning(f"Failed to add discovery '{d.title}': {e}")

        logger.info(f"Discovery complete: {added_count} new improvements found")
        return added_count

    def run(self, discover: bool = False):
        """Main run loop with smart task resumption and priority system.

        Phase ordering (priority):
        1. Resume stuck in-progress tasks (from crashed processes) - HIGHEST PRIORITY
        2. Test tasks that need testing (complete pending work)
        3. Execute approved tasks
        4. Generate plans for pending tasks (by priority)

        Args:
            discover: If True, run improvement discovery before other phases
        """
        if not self.acquire_lock():
            logger.info("Another instance is running, skipping")
            return

        try:
            # Start monitoring at the beginning of run
            self.monitor.start()

            start_time = time.time()
            logger.info("=" * 50)
            logger.info("SelfAI Run Started")
            logger.info("=" * 50)

            stats = self.db.get_stats()
            logger.info(f"Stats: {stats}")

            tasks_processed = 0

            # Phase 0: Discovery (if enabled)
            if discover:
                discovered = self._discover_existing_features()
                logger.info(f"Phase 0: Discovered {discovered} new improvements")

            # Process tasks (single level workflow)
            level = 1

            # PHASE 1: Resume stuck in-progress tasks (HIGHEST PRIORITY)
            # These may be from crashed processes
            stuck_tasks = self.db.get_stuck_in_progress_tasks(limit=MAX_PARALLEL_TASKS)
            if stuck_tasks:
                logger.warning(f"Phase 1: Resuming {len(stuck_tasks)} stuck in-progress tasks...")
                for task in stuck_tasks:
                    logger.info(f"Resuming stuck task #{task['id']}: {task['title']} (started at {task.get('started_at')})")
                self._execute_parallel(stuck_tasks)
                tasks_processed += len(stuck_tasks)

            # PHASE 2: Test tasks that need testing
            testing = self.db.get_features_for_testing_at_level(level, limit=MAX_PARALLEL_TASKS)
            if testing:
                logger.info(f"Phase 2: Testing {len(testing)} tasks...")
                for task in testing:
                    self._run_test(task, level)
                    tasks_processed += 1

            # PHASE 3: Execute approved tasks
            approved = self.db.get_features_for_level(level, limit=MAX_PARALLEL_TASKS)
            if approved:
                logger.info(f"Phase 3: Executing {len(approved)} approved tasks...")
                self._execute_parallel(approved)
                tasks_processed += len(approved)

            # PHASE 4: Generate plans for pending tasks (BY PRIORITY)
            pending = self.db.get_pending_planning_for_level(level, limit=MAX_PARALLEL_TASKS)
            if pending:
                logger.info(f"Phase 4: Planning {len(pending)} tasks (by priority)...")
                for task in pending:
                    logger.info(f"Planning task #{task['id']} (priority: {task.get('priority', 50)}): {task['title']}")
                    if self.db.can_start_new_task():
                        self._generate_plan(task)
                        tasks_processed += 1

            # Phase 5: Log analysis and self-diagnosis
            logger.info("Phase 5: Running log analysis...")
            try:
                analysis = self.log_analyzer.analyze_logs()

                if analysis['issues_found'] > 0:
                    logger.warning(f"Found {analysis['issues_found']} issues in logs")
                    self.log_analyzer.save_issues(analysis['issues'])

                    # Auto-diagnose critical issues
                    critical_issues = [i for i in analysis['issues']
                                     if i['type'] in ['error', 'exception']]
                    for issue in critical_issues[:3]:  # Max 3 auto-fixes per run
                        try:
                            diagnosis = self.log_analyzer.diagnose_and_fix(
                                issue, self.repo_path
                            )
                            logger.info(f"Diagnosed {issue['type']}: {diagnosis.get('diagnosis', 'N/A')}")
                        except Exception as e:
                            logger.error(f"Diagnosis failed for {issue['type']}: {e}")

                # Think about improvements - Re-enabled with better duplicate detection
                if stats.get('completed', 0) > 5:  # After some successful runs
                    improvements = self.log_analyzer.think_about_improvements(
                        stats, self.repo_path
                    )
                    if improvements:
                        logger.info(f"Suggested {len(improvements)} improvements")
                        added_count = 0
                        for imp in improvements:
                            title = imp['title']
                            if self.db.exists(title):
                                logger.debug(f"Skipping duplicate: {title}")
                            else:
                                self.db.add(
                                    title,
                                    imp.get('description', ''),
                                    imp.get('category', 'general'),
                                    imp.get('priority', 50),
                                    'log_analysis'
                                )
                                added_count += 1
                                logger.info(f"Added improvement: {title}")
                        logger.info(f"Added {added_count}/{len(improvements)} new improvements (rest were duplicates)")
            except Exception as e:
                logger.error(f"Log analysis failed: {e}")

            # Update dashboard
            self.update_dashboard()

            duration = time.time() - start_time
            logger.info(f"Run completed: {tasks_processed} tasks in {duration:.1f}s")

            # Log monitoring metrics
            metrics = self.monitor.get_metrics()
            logger.info(f"Monitoring metrics: {metrics}")

        except Exception as e:
            logger.error(f"Run failed: {e}")
        finally:
            # Stop monitoring
            self.monitor.stop()
            self.release_lock()

    def _extract_key_features(self, plan_content: str) -> str:
        """Extract key features from a plan for the optimized summary."""
        try:
            # Try to parse as JSON first
            # Look for JSON block in the content
            json_start = plan_content.find('{')
            json_end = plan_content.rfind('}') + 1
            if json_start >= 0 and json_end > json_start:
                json_str = plan_content[json_start:json_end]
                plan_data = json.loads(json_str)

                # Build summary from key fields
                parts = []
                if plan_data.get('overview'):
                    parts.append(plan_data['overview'][:150])
                if plan_data.get('complexity'):
                    parts.append(f"[{plan_data['complexity']}]")
                if plan_data.get('implementation_steps'):
                    steps = len(plan_data['implementation_steps'])
                    parts.append(f"{steps} steps")

                return ' | '.join(parts)

        except (json.JSONDecodeError, KeyError, TypeError):
            pass

        # Fallback: extract first meaningful line
        lines = [l.strip() for l in plan_content.split('\n') if l.strip() and not l.startswith('```')]
        if lines:
            return lines[0][:150]

        return plan_content[:100]

    def _generate_plan(self, task: Dict):
        """Generate a detailed plan for a task at its current level, reusing existing plan if available."""
        imp_id = task['id']
        title = task['title']
        description = task.get('description', '')
        user_feedback = task.get('user_feedback', '')
        level = task.get('current_level', 1)
        guidance = LEVEL_GUIDANCE[level]

        # Check for plan reuse (for retried tasks)
        existing_plan = self.db.get_plan_for_reuse(imp_id)
        if existing_plan and not user_feedback:
            logger.info(f"Reusing existing plan for #{imp_id}: {title}")
            self.db.save_plan(imp_id, existing_plan)
            return

        logger.info(f"Generating {guidance['name']} plan for #{imp_id}: {title}")
        self.db.mark_planning(imp_id)

        # Build planning prompt with internet research
        feedback_section = ""
        if user_feedback:
            feedback_section = f"""
## User Feedback (incorporate this)
{user_feedback}
"""

        # Get previous level output if advancing from a lower level
        previous_output = ""
        if level > 1:
            prev_level_name = ['mvp', 'enhanced'][level-2]
            prev_output = task.get(f'{prev_level_name}_output', '')
            if prev_output:
                previous_output = f"""
## Previous Level Output
This feature has completed the {prev_level_name.upper()} level. Build upon this implementation:
{prev_output}
"""

        # Format scope and test criteria
        scope_list = '\n'.join(f'  - {s}' for s in guidance['scope'])
        test_list = '\n'.join(f'  - {t}' for t in guidance['test_criteria'])

        prompt = f"""You are planning a {guidance['name']} level implementation for the SelfAI project.

## Task
**Title:** {title}
**Description:** {description}
{feedback_section}
## Level: {guidance['name']} ({level}/3)
{guidance['description']}

## Scope for this level:
{scope_list}

## Test criteria for this level:
{test_list}
{previous_output}
{guidance['prompt_suffix']}

## Instructions
Create a DETAILED implementation plan for THIS LEVEL ONLY. Research best practices from the internet.

Your plan must include:

1. **Overview** - What this feature does and why it's needed
2. **Research** - Best practices from the web (cite sources if possible)
3. **Existing Code Analysis** - What existing code/patterns to leverage
4. **Implementation Steps** - Detailed step-by-step implementation:
   - Each step should be specific and actionable
   - Include file paths to create/modify
   - Include code snippets where helpful
5. **Testing Strategy** - How to test this feature
6. **Risks & Mitigations** - Potential issues and how to handle them
7. **Estimated Complexity** - Low/Medium/High with justification

Format your response as a JSON object:
```json
{{
  "overview": "...",
  "research": [
    {{"topic": "...", "best_practice": "...", "source": "..."}}
  ],
  "existing_code": ["file1.py", "file2.py"],
  "implementation_steps": [
    {{"step": 1, "description": "...", "files": ["..."], "code_snippet": "..."}}
  ],
  "testing_strategy": "...",
  "risks": [
    {{"risk": "...", "mitigation": "..."}}
  ],
  "complexity": "Medium",
  "complexity_reason": "..."
}}
```

Be thorough and detailed. This plan will be reviewed by a human before execution.
"""

        try:
            result = subprocess.run(
                [CLAUDE_CMD, '-p', prompt, '--allowedTools', 'WebSearch,WebFetch,Read,Glob,Grep'],
                capture_output=True,
                text=True,
                timeout=600,  # 10 minutes for complex plans
                cwd=str(self.repo_path)
            )

            if result.returncode == 0 and result.stdout.strip():
                plan_content = result.stdout.strip()
                optimized = self._extract_key_features(plan_content)
                self.db.save_plan(imp_id, plan_content, optimized)
                logger.info(f"Plan generated for #{imp_id}, awaiting review")
            else:
                error = result.stderr or "No output from Claude"
                logger.error(f"Plan generation failed for #{imp_id}: {error}")
                # Reset to pending for retry on next run
                self.db._update_status(imp_id, 'pending')

        except subprocess.TimeoutExpired:
            logger.error(f"Plan generation timed out for #{imp_id}")
            # Reset to pending for retry
            self.db._update_status(imp_id, 'pending')
        except Exception as e:
            logger.error(f"Plan generation error for #{imp_id}: {e}")
            # Reset to pending for retry
            self.db._update_status(imp_id, 'pending')

    def _execute_parallel(self, tasks: List[Dict]):
        """Execute tasks in parallel with proper exception handling and metrics tracking."""
        logger.info(f"Starting parallel execution of {len(tasks)} tasks in isolated worktrees")
        start_time = time.time()

        # Track metrics
        metrics = {
            'tasks_started': len(tasks),
            'tasks_completed': 0,
            'tasks_failed': 0,
            'worktrees_created': 0,
            'merge_conflicts': 0,
            'conflicts_auto_resolved': 0
        }

        with ThreadPoolExecutor(max_workers=MAX_PARALLEL_TASKS) as executor:
            futures = {executor.submit(self._execute_task_in_worktree, task, metrics): task for task in tasks}

            for future in as_completed(futures):
                task = futures[future]
                try:
                    # CRITICAL: Call result() to propagate exceptions
                    future.result()
                    logger.info(f"Task #{task['id']} completed successfully")
                    metrics['tasks_completed'] += 1
                except GitOperationError as e:
                    logger.error(f"Git error for #{task['id']}: {e}")
                    self.db.mark_failed(task['id'], str(e))
                    metrics['tasks_failed'] += 1
                    # Cleanup worktree on failure
                    self.worktree_manager.cleanup_worktree(task['id'], force=True)
                    self.db.clear_worktree_info(task['id'])
                except subprocess.TimeoutExpired:
                    logger.error(f"Task #{task['id']} timed out")
                    self.db.mark_failed(task['id'], "Execution timed out")
                    metrics['tasks_failed'] += 1
                    self.worktree_manager.cleanup_worktree(task['id'], force=True)
                    self.db.clear_worktree_info(task['id'])
                except Exception as e:
                    logger.error(f"Task #{task['id']} failed: {e}", exc_info=True)
                    self.db.mark_failed(task['id'], str(e))
                    metrics['tasks_failed'] += 1
                    self.worktree_manager.cleanup_worktree(task['id'], force=True)
                    self.db.clear_worktree_info(task['id'])

        # Log metrics after execution
        duration = time.time() - start_time
        logger.info(f"Parallel execution complete: {metrics} in {duration:.1f}s")

    def _execute_task_in_worktree(self, task: Dict, metrics: Dict):
        """Execute a single task in an isolated worktree.

        Args:
            task: Task dictionary
            metrics: Shared metrics dictionary for tracking
        """
        imp_id = task['id']
        title = task['title']
        plan_content = task.get('plan_content', '')

        logger.info(f"Executing #{imp_id}: {title}")
        self.db.mark_in_progress(imp_id)

        # Validate task
        try:
            self.worktree_manager.validate_task(task)
        except ValidationError as e:
            logger.error(f"Task validation failed for #{imp_id}: {e}")
            raise GitOperationError(f"Task validation failed: {e}")

        # Create worktree
        worktree_path = self.worktree_manager.create_worktree(imp_id, title)
        if not worktree_path:
            raise GitOperationError(f"Failed to create worktree for #{imp_id}")

        metrics['worktrees_created'] += 1

        # Get branch name
        success, branch_name = self.worktree_manager._run_git(
            'rev-parse', '--abbrev-ref', 'HEAD',
            cwd=worktree_path,
            retry=False
        )

        if success:
            self.db.set_worktree_info(imp_id, str(worktree_path), branch_name)

        prompt = f"""Execute this implementation plan for the SelfAI project.

## Task: {title}

## Plan
{plan_content}

## Instructions
1. Follow the plan step by step
2. Create/modify the necessary files
3. Write clean, well-documented code
4. Follow existing code patterns in the codebase
5. After implementation, commit your changes with a descriptive message

IMPORTANT: Only implement what's in the plan. Do not add extra features.
"""

        try:
            # Execute in worktree context
            result = subprocess.run(
                [CLAUDE_CMD, '-p', prompt, '--allowedTools', 'Read,Write,Edit,Bash,Glob,Grep'],
                capture_output=True,
                text=True,
                timeout=600,
                cwd=str(worktree_path)  # Execute in worktree!
            )

            if result.returncode == 0:
                output = result.stdout.strip()
                self.db.mark_testing(imp_id, output)
                logger.info(f"Execution completed for #{imp_id}, ready for testing")
            else:
                error = result.stderr or "Execution failed"
                logger.error(f"Execution failed for #{imp_id}: {error[:200]}")
                self.db.mark_failed(imp_id, error[:500])
                raise GitOperationError(f"Execution failed: {error[:500]}")

        except subprocess.TimeoutExpired:
            logger.error(f"Execution timed out for #{imp_id}")
            self.db.mark_failed(imp_id, "Execution timed out")
            raise
        except Exception as e:
            logger.error(f"Execution error for #{imp_id}: {e}")
            self.db.mark_failed(imp_id, str(e))
            raise

    def _execute_task(self, task: Dict):
        """Execute a single approved task (legacy method)."""
        imp_id = task['id']
        title = task['title']
        plan_content = task.get('plan_content', '')

        logger.info(f"Executing #{imp_id}: {title}")
        self.db.mark_in_progress(imp_id)

        prompt = f"""Execute this implementation plan for the SelfAI project.

## Task: {title}

## Plan
{plan_content}

## Instructions
1. Follow the plan step by step
2. Create/modify the necessary files
3. Write clean, well-documented code
4. Follow existing code patterns in the codebase
5. After implementation, commit your changes with a descriptive message

IMPORTANT: Only implement what's in the plan. Do not add extra features.
"""

        try:
            result = subprocess.run(
                [CLAUDE_CMD, '-p', prompt, '--allowedTools', 'Read,Write,Edit,Bash,Glob,Grep'],
                capture_output=True,
                text=True,
                timeout=600,
                cwd=str(self.repo_path)
            )

            if result.returncode == 0:
                output = result.stdout.strip()
                self.db.mark_testing(imp_id, output)
                logger.info(f"Execution completed for #{imp_id}, ready for testing")
            else:
                error = result.stderr or "Execution failed"
                logger.error(f"Execution failed for #{imp_id}: {error[:200]}")
                self.db.mark_failed(imp_id, error[:500])

        except subprocess.TimeoutExpired:
            logger.error(f"Execution timed out for #{imp_id}")
            self.db.mark_failed(imp_id, "Execution timed out")
        except Exception as e:
            logger.error(f"Execution error for #{imp_id}: {e}")
            self.db.mark_failed(imp_id, str(e))

    def _get_test_criteria(self, level: int) -> str:
        """Get test criteria string for a specific level."""
        guidance = LEVEL_GUIDANCE[level]
        criteria = '\n'.join(f'  {i+1}. {c}' for i, c in enumerate(guidance['test_criteria']))
        return f"""
## {guidance['name']} Test Criteria (Level {level}/3)
{criteria}
"""

    def _run_test(self, task: Dict, level: int = None):
        """Run tests for a task at a specific level in isolated environment."""
        imp_id = task['id']
        title = task['title']

        # Use provided level or get from task
        if level is None:
            level = task.get('current_level', 1)

        level_test_count_col = {1: 'mvp_test_count', 2: 'enhanced_test_count', 3: 'advanced_test_count'}[level]
        test_count = task.get(level_test_count_col, 0)
        level_name = LEVEL_GUIDANCE[level]['name']

        logger.info(f"Testing {level_name} #{imp_id}: {title} (attempt {test_count + 1}/{MAX_TEST_ATTEMPTS})")

        # Create isolated test environment
        test_env = None
        try:
            test_env = self.test_env_manager.create_environment(imp_id)

            test_criteria = self._get_test_criteria(level)

            prompt = f"""Test the {level_name} level implementation for: {title}

{test_criteria}

Run appropriate tests to verify the feature meets the criteria:
1. Check for syntax errors
2. Run unit tests if they exist
3. Test the feature manually against the criteria
4. Verify no regressions

If tests PASS, respond with: TEST_PASSED
If tests FAIL, respond with: TEST_FAILED followed by the error details
"""

            result = subprocess.run(
                [CLAUDE_CMD, '-p', prompt, '--allowedTools', 'Read,Bash,Glob,Grep'],
                capture_output=True,
                text=True,
                timeout=300,
                cwd=str(test_env.worktree_path or self.repo_path),
                env=test_env.as_subprocess_env()
            )

            output = result.stdout.strip()

            if 'TEST_PASSED' in output:
                self.db.mark_level_test_passed(imp_id, level, output)
                logger.info(f"{level_name} tests passed for #{imp_id}")

                # Check if we should advance to next level or complete
                if level < 3:
                    # Advance to next level
                    self.db.advance_to_next_level(imp_id)
                    logger.info(f"#{imp_id} advanced to level {level + 1}")
                else:
                    # All levels complete - merge and push
                    self._merge_and_push(imp_id, title)
            else:
                # Mark level test as failed (will retry up to MAX_TEST_ATTEMPTS)
                level_test_count_col = {1: 'mvp_test_count', 2: 'enhanced_test_count', 3: 'advanced_test_count'}[level]
                current_count = task.get(level_test_count_col, 0) + 1

                # Update test count
                with self.db.get_connection() as conn:
                    conn.execute(f'UPDATE improvements SET {level_test_count_col} = ? WHERE id = ?',
                               (current_count, imp_id))

                if current_count >= MAX_TEST_ATTEMPTS:
                    logger.warning(f"{level_name} tests failed {MAX_TEST_ATTEMPTS} times for #{imp_id}, marking as cancelled")
                    self.db._update_status(imp_id, 'cancelled')
                else:
                    logger.warning(f"{level_name} tests failed for #{imp_id} (attempt {current_count}/{MAX_TEST_ATTEMPTS})")

        except subprocess.TimeoutExpired:
            logger.warning(f"{level_name} test timed out for #{imp_id}")
        except Exception as e:
            logger.error(f"Test execution error for #{imp_id}: {e}")
        finally:
            # Always cleanup the test environment
            if test_env:
                self.test_env_manager.release_environment(imp_id)

    def _merge_and_push(self, imp_id: int, title: str):
        """Merge worktree branch to main and push to origin with conflict handling."""
        try:
            # Attempt merge
            success, message = self.worktree_manager.merge_to_main(imp_id, title)

            if not success:
                # Check for conflicts
                has_conflicts, conflicted_files = self.worktree_manager._detect_merge_conflicts()

                if has_conflicts:
                    logger.warning(f"Merge conflicts detected for #{imp_id}: {conflicted_files}")
                    self.db.record_merge_conflict(imp_id, conflicted_files)

                    # Attempt automatic resolution with Claude
                    resolved = self.worktree_manager.resolve_conflicts_with_claude(
                        imp_id, title, conflicted_files
                    )

                    if not resolved:
                        # Manual intervention required
                        self.db.mark_failed(
                            imp_id,
                            f"Merge conflicts require manual resolution: {', '.join(conflicted_files)}"
                        )
                        return False
                else:
                    # Non-conflict merge failure
                    logger.error(f"Merge failed for #{imp_id}: {message}")
                    self.db.mark_failed(imp_id, f"Merge failed: {message}")
                    return False

            # Push to origin
            result = subprocess.run(
                ['git', 'push', 'origin', 'main'],
                cwd=str(self.repo_path),
                capture_output=True,
                text=True
            )

            if result.returncode == 0:
                logger.info(f"Successfully merged and pushed #{imp_id}")
                # Cleanup worktree after successful merge
                self.worktree_manager.cleanup_worktree(imp_id)
                self.db.clear_worktree_info(imp_id)
                return True
            else:
                logger.error(f"Push failed for #{imp_id}: {result.stderr}")
                self.db.mark_failed(imp_id, f"Push failed: {result.stderr}")
                return False

        except Exception as e:
            logger.error(f"Merge/push error for #{imp_id}: {e}")
            self.db.mark_failed(imp_id, str(e))
            return False

    def update_dashboard(self):
        """Update the HTML dashboard."""
        stats = self.db.get_stats()
        tasks = self.db.get_all()
        discovery_stats = self.db.get_discovery_stats()

        # Generate HTML
        html = self._generate_dashboard_html(stats, tasks, discovery_stats)

        # Write dashboard
        dashboard_path = self.data_dir / 'dashboard.html'
        dashboard_path.write_text(html)
        logger.debug(f"Dashboard updated: {stats}")

    def _generate_discovery_stats_html(self, discovery_stats: Dict) -> str:
        """Generate discovery statistics HTML section."""
        if not discovery_stats:
            return ''

        # Category icons
        category_icons = {
            'security': '🔒',
            'test_coverage': '🧪',
            'refactoring': '🔧',
            'documentation': '📝',
            'performance': '⚡',
            'code_quality': '✨'
        }

        stat_cards = []
        for category, count in discovery_stats.items():
            icon = category_icons.get(category, '🔍')
            display_name = category.replace('_', ' ').title()
            stat_cards.append(f'''
            <div class="stat-card" style="background: rgba(123, 44, 191, 0.2);">
                <div class="value" style="color: #a78bfa">{icon} {count}</div>
                <div class="label">{display_name}</div>
            </div>
            ''')

        return f'''
        <div style="margin: 20px 0;">
            <h3 style="text-align: center; color: #a78bfa; margin-bottom: 10px;">🔍 Discovered Improvements</h3>
            <div class="stats">
                {''.join(stat_cards)}
            </div>
        </div>
        '''

    def _generate_dashboard_html(self, stats: Dict, tasks: List[Dict], discovery_stats: Dict) -> str:
        """Generate dashboard HTML."""
        # Get recovery stats
        recovery_stats = self.db.get_recovery_stats()
        stuck_count = recovery_stats.get('stuck_count', 0)

        # Status colors
        status_colors = {
            'pending': '#6b7280',
            'planning': '#8b5cf6',
            'plan_review': '#f59e0b',
            'approved': '#06b6d4',  # Cyan - ready for execution
            'in_progress': '#3b82f6',
            'testing': '#6366f1',
            'completed': '#22c55e',
            'failed': '#ef4444',
            'cancelled': '#dc2626',
        }

        # Generate task rows and plan data for JavaScript
        rows = []
        plans_data = {}
        for task in tasks:
            status = task.get('status', 'pending')
            color = status_colors.get(status, '#6b7280')

            # Plan content
            plan = task.get('plan_content', '') or ''
            optimized = task.get('optimized_plan', '') or ''

            # Display optimized plan if available, otherwise plan preview
            display_text = optimized if optimized else plan[:100]
            display_preview = display_text[:80].replace('"', '&quot;').replace('<', '&lt;').replace('\n', ' ')

            # Store plan data for JavaScript - escape </script> to prevent breaking HTML
            if plan:
                # Must escape </script> or it will close the script tag prematurely
                safe_plan = plan.replace('</script>', '<\\/script>')
                plans_data[task['id']] = safe_plan

            # Worktree info
            worktree_info = ''
            branch_name = task.get('branch_name', '')
            if branch_name:
                worktree_info = f'<br><small style="color: #8b5cf6;">🌿 {branch_name}</small>'

            # Conflict indicator
            merge_conflicts = task.get('merge_conflicts', '')
            if merge_conflicts:
                import json as json_lib
                try:
                    conflicts = json_lib.loads(merge_conflicts) if isinstance(merge_conflicts, str) else merge_conflicts
                    conflict_count = len(conflicts) if isinstance(conflicts, list) else 0
                    if conflict_count > 0:
                        worktree_info += f'<br><small style="color: #ef4444;">⚠️ {conflict_count} conflicts</small>'
                except:
                    pass

            # Action buttons based on status
            actions = ''
            if plan:
                actions += f'''<button onclick="showPlan({task['id']})" class="btn-view">View Plan</button>'''
            if status == 'cancelled':
                actions += f'''
                    <button onclick="reEnable({task['id']})" class="btn-reenable">Re-enable</button>
                '''

            test_info = f"{task.get('test_count', 0)}/{MAX_TEST_ATTEMPTS}" if status in ['failed', 'cancelled', 'testing'] else '-'

            rows.append(f'''
            <tr class="{status}">
                <td>{task['id']}</td>
                <td>{task['title']}{worktree_info}</td>
                <td><span class="status-badge" style="background: {color}20; color: {color}">{status}</span></td>
                <td class="plan-cell">{display_preview}{'...' if len(display_text) > 80 else '' if display_text else '<em>Pending</em>'}</td>
                <td>{test_info}</td>
                <td>{actions}</td>
            </tr>
            ''')

        return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SelfAI Dashboard</title>
    <meta http-equiv="refresh" content="30">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            min-height: 100vh;
            padding: 20px;
            color: #fff;
        }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        h1 {{
            text-align: center;
            margin-bottom: 20px;
            background: linear-gradient(90deg, #00d4ff, #7b2cbf);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .stats {{
            display: flex;
            gap: 15px;
            flex-wrap: wrap;
            justify-content: center;
            margin-bottom: 20px;
        }}
        .stat-card {{
            background: rgba(255,255,255,0.1);
            padding: 15px 25px;
            border-radius: 10px;
            text-align: center;
        }}
        .stat-card .value {{ font-size: 1.5rem; font-weight: bold; }}
        .stat-card .label {{ color: #888; font-size: 0.8rem; }}
        table {{
            width: 100%;
            border-collapse: collapse;
            background: rgba(255,255,255,0.05);
            border-radius: 10px;
            overflow: hidden;
        }}
        th {{ background: rgba(255,255,255,0.1); padding: 12px; text-align: left; }}
        td {{ padding: 10px 12px; border-bottom: 1px solid rgba(255,255,255,0.05); }}
        .status-badge {{
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 0.75rem;
            font-weight: 600;
        }}
        .plan-cell {{
            max-width: 300px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            color: #888;
            font-size: 0.85rem;
        }}
        .btn-approve, .btn-feedback, .btn-reenable, .btn-view {{
            padding: 5px 10px;
            border: none;
            border-radius: 5px;
            cursor: pointer;
            font-size: 0.75rem;
            margin: 2px;
        }}
        .btn-approve {{ background: #22c55e; color: white; }}
        .btn-feedback {{ background: #f59e0b; color: white; }}
        .btn-reenable {{ background: #6366f1; color: white; }}
        .btn-view {{ background: #3b82f6; color: white; }}
        tr.plan_review {{ background: rgba(245, 158, 11, 0.1); }}
        tr.cancelled {{ background: rgba(220, 38, 38, 0.1); opacity: 0.7; }}
        tr.completed {{ opacity: 0.6; }}

        /* Modal */
        .modal {{
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.8);
            justify-content: center;
            align-items: center;
        }}
        .modal-content {{
            background: #1a1a2e;
            padding: 30px;
            border-radius: 15px;
            max-width: 600px;
            width: 90%;
        }}
        .modal-content.wide {{
            max-width: 90%;
            max-height: 90vh;
            overflow-y: auto;
        }}
        .plan-content {{
            background: #16213e;
            padding: 20px;
            border-radius: 8px;
            white-space: pre-wrap;
            font-family: monospace;
            font-size: 0.85rem;
            max-height: 60vh;
            overflow-y: auto;
            line-height: 1.5;
        }}
        .modal textarea {{
            width: 100%;
            height: 150px;
            margin: 15px 0;
            padding: 10px;
            border-radius: 8px;
            border: 1px solid #333;
            background: #16213e;
            color: #fff;
        }}
        .modal button {{
            padding: 10px 20px;
            margin: 5px;
            border: none;
            border-radius: 8px;
            cursor: pointer;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>SelfAI Dashboard</h1>
        <p style="text-align: center; color: #888; margin-bottom: 20px;">
            Planning-First Workflow | Max {MAX_PARALLEL_TASKS} Parallel | {MAX_TEST_ATTEMPTS} Test Attempts
        </p>

        {'<div class="warning-banner" style="background: rgba(245, 158, 11, 0.2); padding: 15px; border-radius: 8px; margin-bottom: 20px; border-left: 4px solid #f59e0b;">' +
         f'⚠️ <strong>{stuck_count} stuck in-progress task(s)</strong> detected (may be from crashed processes)' +
         '<br><small>Will be resumed on next run</small></div>' if stuck_count > 0 else ''}

        <div class="stats">
            <div class="stat-card">
                <div class="value" style="color: #f59e0b">{stats.get('plan_review', 0)}</div>
                <div class="label">Awaiting Review</div>
            </div>
            <div class="stat-card">
                <div class="value" style="color: #10b981">{stats.get('approved', 0)}</div>
                <div class="label">Approved</div>
            </div>
            <div class="stat-card">
                <div class="value" style="color: #3b82f6">{stats.get('in_progress', 0)}</div>
                <div class="label">In Progress</div>
            </div>
            <div class="stat-card">
                <div class="value" style="color: #22c55e">{stats.get('completed', 0)}</div>
                <div class="label">Completed</div>
            </div>
            <div class="stat-card">
                <div class="value" style="color: #dc2626">{stats.get('cancelled', 0)}</div>
                <div class="label">Cancelled</div>
            </div>
            <div class="stat-card">
                <div class="value" style="color: #f59e0b">{stuck_count}</div>
                <div class="label">Stuck Tasks</div>
            </div>
        </div>

        {self._generate_discovery_stats_html(discovery_stats) if discovery_stats else ''}

        <table>
            <thead>
                <tr>
                    <th>ID</th>
                    <th>Feature</th>
                    <th>Status</th>
                    <th>Key Features</th>
                    <th>Tests</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody>
                {''.join(rows)}
            </tbody>
        </table>
    </div>

    <!-- Feedback Modal -->
    <div id="feedbackModal" class="modal">
        <div class="modal-content">
            <h3>Provide Feedback</h3>
            <p>Your feedback will be incorporated into a revised plan:</p>
            <textarea id="feedbackText" placeholder="Describe what changes you want..."></textarea>
            <button onclick="submitFeedback()" style="background: #f59e0b; color: white;">Submit</button>
            <button onclick="closeModal()" style="background: #6b7280; color: white;">Cancel</button>
        </div>
    </div>

    <!-- Plan Modal -->
    <div id="planModal" class="modal">
        <div class="modal-content wide">
            <h3 id="planTitle">Plan Details</h3>
            <div id="planContent" class="plan-content"></div>
            <div style="margin-top: 15px; text-align: right;">
                <button onclick="closePlanModal()" style="background: #6b7280; color: white;">Close</button>
            </div>
        </div>
    </div>

    <script>
        let currentTaskId = null;
        const plans = {json.dumps(plans_data)};

        function showToast(msg, isError) {{
            const toast = document.createElement('div');
            toast.style.cssText = 'position:fixed;bottom:20px;right:20px;padding:15px 25px;border-radius:8px;color:white;z-index:10000;background:' + (isError ? '#ef4444' : '#22c55e');
            toast.textContent = msg;
            document.body.appendChild(toast);
            setTimeout(() => toast.remove(), 3000);
        }}

        async function apiCall(endpoint, method, body) {{
            try {{
                const response = await fetch(endpoint, {{
                    method: method,
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: body ? JSON.stringify(body) : undefined
                }});
                const data = await response.json();
                if (data.success) {{
                    showToast(data.message);
                    setTimeout(() => location.reload(), 1000);
                }} else {{
                    showToast(data.error || 'Request failed', true);
                }}
            }} catch (e) {{
                showToast('Server not running. Start with: python -m selfai serve', true);
            }}
        }}

        function showPlan(id) {{
            const plan = plans[id];
            const modal = document.getElementById('planModal');
            const title = document.getElementById('planTitle');
            const content = document.getElementById('planContent');
            if (plan && modal && title && content) {{
                title.textContent = 'Plan for Task #' + id;
                content.textContent = plan;
                modal.style.display = 'flex';
            }} else {{
                alert('Plan not found for task #' + id);
            }}
        }}

        function closePlanModal() {{
            document.getElementById('planModal').style.display = 'none';
        }}

        function approvePlan(id) {{
            if (confirm('Approve plan for task #' + id + '?')) {{
                apiCall('/api/approve/' + id, 'POST');
            }}
        }}

        function showFeedback(id) {{
            currentTaskId = id;
            document.getElementById('feedbackModal').style.display = 'flex';
        }}

        function closeModal() {{
            document.getElementById('feedbackModal').style.display = 'none';
        }}

        function submitFeedback() {{
            const feedback = document.getElementById('feedbackText').value;
            if (feedback) {{
                apiCall('/api/feedback/' + currentTaskId, 'POST', {{ feedback: feedback }});
            }}
            closeModal();
        }}

        function reEnable(id) {{
            const feedback = prompt('Optional feedback for re-enabling task #' + id + ':', '');
            if (feedback !== null) {{
                apiCall('/api/reenable/' + id, 'POST', {{ feedback: feedback }});
            }}
        }}

        // Close modals when clicking outside
        document.addEventListener('click', function(e) {{
            if (e.target.classList.contains('modal')) {{
                e.target.style.display = 'none';
            }}
        }});
    </script>
</body>
</html>'''


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description='SelfAI Runner')
    parser.add_argument('command', nargs='?', default='run',
                        choices=['run', 'status', 'approve', 'feedback', 'reenable', 'add'])
    parser.add_argument('task_id', nargs='?', type=int)
    parser.add_argument('message', nargs='?')

    args = parser.parse_args()

    repo_path = Path.cwd()
    runner = SelfAIRunner(repo_path)

    if args.command == 'run':
        runner.run()
    elif args.command == 'status':
        stats = runner.db.get_stats()
        print("SelfAI Status:")
        for status, count in stats.items():
            if count > 0:
                print(f"  {status}: {count}")
    elif args.command == 'approve' and args.task_id:
        runner.db.approve_plan(args.task_id)
        print(f"Approved plan for task #{args.task_id}")
    elif args.command == 'feedback' and args.task_id and args.message:
        runner.db.request_plan_feedback(args.task_id, args.message)
        print(f"Feedback submitted for task #{args.task_id}")
    elif args.command == 'reenable' and args.task_id:
        runner.db.re_enable_cancelled(args.task_id, args.message or '')
        print(f"Re-enabled task #{args.task_id}")
    elif args.command == 'add' and args.message:
        title = args.message
        task_id = runner.db.add(title, '')
        print(f"Added task #{task_id}: {title}")


if __name__ == '__main__':
    main()
