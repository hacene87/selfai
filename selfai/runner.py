"""Autonomous improvement runner with 3-level progressive complexity.

Each feature progresses through:
1. MVP: Simple, working implementation
2. Enhanced: Robust with edge cases, better error handling
3. Advanced: Production-ready, optimized, comprehensive

At each level: Plan → Execute → Test → (Pass: Next Level, Fail: Retry)

PARALLEL PROCESSING WITH GIT WORKTREES:
- Each task runs in its own git worktree (isolated branch)
- Main branch stays clean
- Successful tests merge to main automatically
- Conflicts resolved with Claude assistance
- Up to 5 parallel tasks supported

SELF-IMPROVEMENT & LOG ANALYSIS:
- Analyzes logs before/during/after each task
- Diagnoses and fixes issues automatically
- Learns from patterns to improve itself
"""
import os
import subprocess
import logging
import json
import html
import time
import re
import shutil
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from .database import Database, LEVEL_NAMES

logger = logging.getLogger('selfai')


# Custom Exceptions
class WorktreeError(Exception):
    """Base exception for worktree operations."""
    pass


class MergeConflictError(WorktreeError):
    """Raised when merge conflicts cannot be resolved."""
    pass


class DiskSpaceError(WorktreeError):
    """Raised when insufficient disk space is available."""
    pass


class ValidationError(WorktreeError):
    """Raised when input validation fails."""
    pass


class WorktreeManager:
    """Manage git worktrees for parallel task execution."""

    # Constants
    MIN_DISK_SPACE_MB = 500  # Minimum disk space required (MB)
    MAX_RETRIES = 3  # Max retries for transient failures
    RETRY_DELAY = 2  # Base delay between retries (seconds)

    def __init__(self, repo_path: Path, workspace_path: Path):
        self.repo_path = repo_path
        self.worktrees_path = workspace_path / 'worktrees'
        self.worktrees_path.mkdir(parents=True, exist_ok=True)

        # Cleanup orphaned worktrees on startup
        self._cleanup_orphaned_worktrees()

    def _run_git(self, *args, cwd: Path = None, retry: bool = True) -> Tuple[bool, str]:
        """Run a git command with retry logic and return (success, output)."""
        retries = self.MAX_RETRIES if retry else 1

        for attempt in range(retries):
            try:
                result = subprocess.run(
                    ['git'] + list(args),
                    capture_output=True, text=True, timeout=60,
                    cwd=str(cwd or self.repo_path)
                )
                success = result.returncode == 0
                output = result.stdout + result.stderr

                if success or not retry:
                    return success, output

                # Retry for transient failures
                if attempt < retries - 1:
                    delay = self.RETRY_DELAY * (2 ** attempt)  # Exponential backoff
                    logger.warning(f"Git command failed (attempt {attempt + 1}/{retries}), retrying in {delay}s: {args}")
                    time.sleep(delay)
                    continue

                return False, output

            except subprocess.TimeoutExpired:
                if attempt < retries - 1:
                    logger.warning(f"Git command timeout (attempt {attempt + 1}/{retries}), retrying: {args}")
                    continue
                return False, "Git command timed out"
            except Exception as e:
                if attempt < retries - 1:
                    logger.warning(f"Git command exception (attempt {attempt + 1}/{retries}): {e}")
                    continue
                return False, str(e)

        return False, "All retry attempts failed"

    def validate_improvement(self, improvement: Dict) -> None:
        """Validate improvement structure.

        Args:
            improvement: Improvement dict from database

        Raises:
            ValidationError: If improvement is invalid
        """
        if not improvement:
            raise ValidationError("Improvement is None or empty")

        required_fields = ['id', 'title']
        for field in required_fields:
            if field not in improvement or not improvement[field]:
                raise ValidationError(f"Missing required field: {field}")

        # Validate ID is positive integer
        try:
            imp_id = int(improvement['id'])
            if imp_id <= 0:
                raise ValidationError(f"Invalid improvement ID: {imp_id}")
        except (ValueError, TypeError) as e:
            raise ValidationError(f"Invalid improvement ID type: {e}")

        # Validate title length and content
        title = str(improvement['title'])
        if len(title) < 3:
            raise ValidationError(f"Title too short (min 3 chars): {title}")
        if len(title) > 200:
            raise ValidationError(f"Title too long (max 200 chars): {title[:50]}...")

    def validate_repository_state(self) -> None:
        """Validate repository is in clean state.

        Raises:
            ValidationError: If repository has uncommitted changes
        """
        # Check for uncommitted changes
        success, output = self._run_git('status', '--porcelain', retry=False)
        if not success:
            raise ValidationError(f"Cannot check repository status: {output}")

        # Allow changes only in .selfai_data directory
        if output.strip():
            lines = [line for line in output.strip().split('\n')
                    if line and not '.selfai_data' in line]
            if lines:
                raise ValidationError(
                    f"Repository has uncommitted changes. Please commit or stash them first.\n"
                    f"Changed files: {', '.join([l.strip()[:50] for l in lines[:3]])}"
                )

    def check_disk_space(self) -> None:
        """Check available disk space.

        Raises:
            DiskSpaceError: If insufficient disk space
        """
        try:
            stat = os.statvfs(str(self.worktrees_path))
            available_mb = (stat.f_bavail * stat.f_frsize) / (1024 * 1024)

            if available_mb < self.MIN_DISK_SPACE_MB:
                raise DiskSpaceError(
                    f"Insufficient disk space: {available_mb:.0f}MB available, "
                    f"{self.MIN_DISK_SPACE_MB}MB required"
                )

            logger.debug(f"Disk space check OK: {available_mb:.0f}MB available")

        except DiskSpaceError:
            raise
        except Exception as e:
            logger.warning(f"Cannot check disk space: {e}")
            # Don't fail on disk space check errors

    def _cleanup_orphaned_worktrees(self) -> None:
        """Cleanup orphaned worktrees from crashed processes."""
        try:
            # Get list of worktrees from git
            success, output = self._run_git('worktree', 'list', '--porcelain', retry=False)
            if not success:
                logger.debug("Cannot list worktrees, skipping orphan cleanup")
                return

            git_worktrees = set()
            for line in output.split('\n'):
                if line.startswith('worktree '):
                    path = Path(line.replace('worktree ', '').strip())
                    if 'wt-' in path.name:
                        git_worktrees.add(path)

            # Get worktree directories on disk
            disk_worktrees = set()
            if self.worktrees_path.exists():
                disk_worktrees = {p for p in self.worktrees_path.iterdir()
                                 if p.is_dir() and p.name.startswith('wt-')}

            # Find orphans (in disk but not in git, or vice versa)
            orphaned_on_disk = disk_worktrees - git_worktrees
            orphaned_in_git = git_worktrees - disk_worktrees

            # Cleanup orphaned directories
            for orphan in orphaned_on_disk:
                try:
                    logger.info(f"Removing orphaned worktree directory: {orphan.name}")
                    shutil.rmtree(orphan, ignore_errors=True)
                except Exception as e:
                    logger.warning(f"Failed to remove orphaned directory {orphan}: {e}")

            # Cleanup orphaned git entries
            for orphan in orphaned_in_git:
                try:
                    logger.info(f"Removing orphaned worktree entry: {orphan.name}")
                    self._run_git('worktree', 'remove', str(orphan), '--force', retry=False)
                except Exception as e:
                    logger.warning(f"Failed to remove orphaned worktree {orphan}: {e}")

            # Prune stale entries
            self._run_git('worktree', 'prune', retry=False)

            if orphaned_on_disk or orphaned_in_git:
                logger.info(f"Cleaned up {len(orphaned_on_disk) + len(orphaned_in_git)} orphaned worktrees")

        except Exception as e:
            logger.warning(f"Orphan cleanup failed: {e}")

    def _sanitize_branch_name(self, title: str) -> str:
        """Convert feature title to valid git branch name, handling special characters.

        Args:
            title: Raw feature title

        Returns:
            Sanitized branch name in format: feature/{sanitized-name}
        """
        # Remove/replace invalid characters
        # Valid: alphanumeric, dash, underscore
        name = re.sub(r'[^\w\s-]', '', title, flags=re.UNICODE)  # Remove special chars
        name = re.sub(r'[\s_]+', '-', name)  # Replace spaces/underscores with dash
        name = re.sub(r'-+', '-', name)  # Collapse multiple dashes
        name = name.strip('-').lower()  # Remove leading/trailing dashes, lowercase

        # Ensure not empty
        if not name:
            name = 'feature'

        # Limit length
        name = name[:50]

        return f"feature/{name}"

    def _resolve_name_collision(self, base_path: Path, max_attempts: int = 100) -> Optional[Path]:
        """Resolve worktree path collision by appending counter.

        Args:
            base_path: Base worktree path that collides
            max_attempts: Maximum number of collision resolution attempts

        Returns:
            Unique path or None if all attempts exhausted
        """
        if not base_path.exists():
            return base_path

        # Try appending timestamp first
        timestamp = datetime.now().strftime('%H%M%S')
        timestamped_path = Path(str(base_path) + f"-{timestamp}")
        if not timestamped_path.exists():
            return timestamped_path

        # Fallback to counter
        for i in range(1, max_attempts):
            new_path = Path(str(base_path) + f"-{i}")
            if not new_path.exists():
                return new_path

        logger.error(f"Cannot resolve name collision after {max_attempts} attempts: {base_path}")
        return None

    def _fetch_main_branch(self) -> Tuple[bool, str]:
        """Fetch latest main branch from remote.

        Returns:
            Tuple of (success, message)
        """
        # Check if remote exists
        success, output = self._run_git('remote', retry=False)
        if not success or not output.strip():
            return True, "No remote configured, skipping fetch"

        # Fetch main branch
        success, output = self._run_git('fetch', 'origin', 'main')
        if not success:
            return False, f"Failed to fetch main branch: {output}"

        return True, "Main branch updated"

    def _detect_merge_conflicts(self) -> Tuple[bool, List[str]]:
        """Detect merge conflicts in current repository.

        Returns:
            Tuple of (has_conflicts, list of conflicted files)
        """
        success, output = self._run_git('diff', '--name-only', '--diff-filter=U', retry=False)
        if not success:
            return False, []

        conflicted_files = [f.strip() for f in output.strip().split('\n') if f.strip()]
        return len(conflicted_files) > 0, conflicted_files

    def create_worktree(self, feature_id: int, feature_title: str) -> Optional[Path]:
        """Create a worktree for a feature task with validation and collision handling.

        Args:
            feature_id: Unique feature ID
            feature_title: Feature title (will be sanitized for branch name)

        Returns:
            The worktree path or None if failed

        Raises:
            ValidationError: If inputs are invalid
            DiskSpaceError: If insufficient disk space
            WorktreeError: If worktree creation fails
        """
        try:
            # Input validation
            if not isinstance(feature_id, int) or feature_id <= 0:
                raise ValidationError(f"Invalid feature_id: {feature_id}")
            if not feature_title or not isinstance(feature_title, str):
                raise ValidationError(f"Invalid feature_title: {feature_title}")

            # Pre-flight checks
            self.check_disk_space()

            # Sanitize branch name
            branch_name = self._sanitize_branch_name(f"{feature_id}-{feature_title}")
            base_worktree_path = self.worktrees_path / f"wt-{feature_id}"

            # Resolve name collision if exists
            worktree_path = self._resolve_name_collision(base_worktree_path)
            if not worktree_path:
                raise WorktreeError(f"Cannot resolve worktree path collision for feature #{feature_id}")

            # Clean up existing worktree if any
            if base_worktree_path.exists():
                self.cleanup_worktree(feature_id)

            # Create branch from main (with retry)
            success, output = self._run_git('branch', branch_name, 'main')
            if not success:
                # Branch might already exist, try to reset it
                logger.info(f"Branch {branch_name} exists, recreating...")
                self._run_git('branch', '-D', branch_name, retry=False)
                success, output = self._run_git('branch', branch_name, 'main')
                if not success:
                    raise WorktreeError(
                        f"Failed to create branch {branch_name} for feature #{feature_id}: {output}"
                    )

            # Create worktree (with retry)
            success, output = self._run_git('worktree', 'add', str(worktree_path), branch_name)
            if not success:
                # Cleanup branch on failure
                self._run_git('branch', '-D', branch_name, retry=False)
                raise WorktreeError(
                    f"Failed to create worktree for feature #{feature_id} at {worktree_path}: {output}"
                )

            logger.info(f"Created worktree for feature #{feature_id}: {worktree_path.name} (branch: {branch_name})")
            return worktree_path

        except (ValidationError, DiskSpaceError, WorktreeError) as e:
            logger.error(f"Worktree creation failed for feature #{feature_id}: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error creating worktree for feature #{feature_id}: {e}")
            raise WorktreeError(f"Unexpected worktree creation error: {e}")

    def cleanup_worktree(self, feature_id: int) -> None:
        """Remove a worktree and its branch with enhanced error handling.

        Args:
            feature_id: Feature ID to cleanup

        Note:
            This method logs errors but does not raise exceptions to allow
            cleanup to continue even if some operations fail.
        """
        worktree_path = self.worktrees_path / f"wt-{feature_id}"

        if not worktree_path.exists():
            # Also check for collision-resolved paths (wt-{id}-timestamp or wt-{id}-N)
            pattern = f"wt-{feature_id}-*"
            matches = list(self.worktrees_path.glob(pattern))
            if matches:
                worktree_path = matches[0]  # Use first match
            else:
                logger.debug(f"Worktree path does not exist, skipping cleanup: {worktree_path}")
                return

        try:
            # Get branch name before removing
            branch_name = None
            success, output = self._run_git('worktree', 'list', '--porcelain', retry=False)
            if success:
                lines = output.split('\n')
                for i, line in enumerate(lines):
                    if str(worktree_path) in line:
                        # Branch name is in the "branch" line after worktree line
                        for j in range(i + 1, min(i + 5, len(lines))):
                            if lines[j].startswith('branch '):
                                branch_name = lines[j].replace('branch refs/heads/', '').strip()
                                break
                        break

            # Remove worktree from git
            success, output = self._run_git('worktree', 'remove', str(worktree_path), '--force', retry=False)
            if not success:
                logger.warning(f"Git worktree remove failed for {worktree_path.name}: {output}")

            # Force remove directory if still exists (locked worktree, etc.)
            if worktree_path.exists():
                try:
                    shutil.rmtree(worktree_path, ignore_errors=False)
                    logger.info(f"Force removed worktree directory: {worktree_path.name}")
                except PermissionError:
                    logger.error(f"Permission denied removing locked worktree: {worktree_path.name}")
                except Exception as e:
                    logger.warning(f"Failed to remove worktree directory: {e}")
                    # Last resort: ignore_errors=True
                    shutil.rmtree(worktree_path, ignore_errors=True)

            # Cleanup branch if found
            if branch_name:
                success, output = self._run_git('branch', '-D', branch_name, retry=False)
                if success:
                    logger.debug(f"Removed branch: {branch_name}")
                else:
                    logger.debug(f"Branch cleanup failed (may not exist): {branch_name}")

        except Exception as e:
            logger.warning(f"Worktree cleanup error for feature #{feature_id}: {e}")
        finally:
            # Always prune stale entries
            try:
                self._run_git('worktree', 'prune', retry=False)
            except Exception as e:
                logger.debug(f"Worktree prune failed: {e}")

    def merge_to_main(self, feature_id: int, feature_title: str) -> Tuple[bool, str]:
        """Merge feature branch to main after tests pass with enhanced error handling.

        Args:
            feature_id: Feature ID
            feature_title: Feature title

        Returns:
            Tuple of (success, message)

        Note:
            Fetches latest main branch before merge to ensure up-to-date
        """
        try:
            branch_name = self._sanitize_branch_name(f"{feature_id}-{feature_title}")

            # Fetch latest main branch first
            fetch_success, fetch_msg = self._fetch_main_branch()
            if not fetch_success:
                logger.warning(f"Failed to fetch main branch: {fetch_msg}")
                # Continue anyway - might be offline or no remote

            # Checkout main
            success, output = self._run_git('checkout', 'main')
            if not success:
                raise WorktreeError(f"Failed to checkout main for feature #{feature_id}: {output}")

            # Pull latest (with rebase to avoid merge commits)
            success, output = self._run_git('pull', '--rebase', retry=False)
            if not success:
                logger.warning(f"Pull rebase failed (may be no remote): {output}")
                # Continue - local repo or no changes

            # Check if branch exists
            success, output = self._run_git('rev-parse', '--verify', branch_name, retry=False)
            if not success:
                return False, f"Branch {branch_name} does not exist for feature #{feature_id}"

            # Merge feature branch
            commit_msg = f"Merge {branch_name}: {feature_title}\n\nFeature #{feature_id}"
            success, output = self._run_git('merge', branch_name, '--no-edit', '-m', commit_msg)

            if not success:
                # Check for merge conflicts
                has_conflicts, conflicted_files = self._detect_merge_conflicts()
                if has_conflicts:
                    # Abort merge
                    self._run_git('merge', '--abort', retry=False)
                    raise MergeConflictError(
                        f"Merge conflicts detected for feature #{feature_id} in files: "
                        f"{', '.join(conflicted_files[:5])}"
                    )
                else:
                    raise WorktreeError(f"Merge failed for feature #{feature_id}: {output}")

            # Push to remote (if configured)
            success, output = self._run_git('push', retry=False)
            if not success:
                logger.warning(f"Push failed (may be no remote configured): {output}")
                # Don't fail merge if push fails - might be offline

            # Cleanup branch
            success, output = self._run_git('branch', '-d', branch_name, retry=False)
            if not success:
                logger.warning(f"Failed to delete branch {branch_name}: {output}")
                # Don't fail merge if branch deletion fails

            logger.info(f"Successfully merged feature #{feature_id} to main: {feature_title}")
            return True, "Merged successfully"

        except MergeConflictError as e:
            logger.error(str(e))
            return False, f"MERGE_CONFLICT: {str(e)}"
        except WorktreeError as e:
            logger.error(str(e))
            return False, f"MERGE_FAILED: {str(e)}"
        except Exception as e:
            logger.error(f"Unexpected merge error for feature #{feature_id}: {e}")
            # Try to abort any in-progress merge
            self._run_git('merge', '--abort', retry=False)
            return False, f"Unexpected merge error: {e}"

    def resolve_conflicts(self, claude_cmd: str, feature_title: str) -> bool:
        """Use Claude to resolve merge conflicts."""
        # Get conflicted files
        success, output = self._run_git('diff', '--name-only', '--diff-filter=U')
        if not success or not output.strip():
            return False

        conflicted_files = output.strip().split('\n')
        logger.info(f"Resolving conflicts in: {conflicted_files}")

        for file_path in conflicted_files:
            prompt = f'''Resolve the git merge conflict in this file.

File: {file_path}
Feature being merged: {feature_title}

Read the file, understand both versions, and create a merged version that:
1. Keeps all functionality from both versions
2. Resolves any logical conflicts intelligently
3. Maintains code quality

After resolving, the file should have NO conflict markers (<<<, ===, >>>).'''

            try:
                subprocess.run(
                    [claude_cmd, '-p', prompt, '--allowedTools', 'Read', 'Edit', 'Write'],
                    capture_output=True, timeout=300, cwd=str(self.repo_path)
                )
                # Stage resolved file
                self._run_git('add', file_path)
            except Exception as e:
                logger.error(f"Failed to resolve conflict in {file_path}: {e}")
                return False

        # Complete the merge
        success, _ = self._run_git('commit', '--no-edit')
        return success

    def get_active_worktrees(self) -> List[Path]:
        """List all active worktrees."""
        success, output = self._run_git('worktree', 'list', '--porcelain')
        if not success:
            return []

        worktrees = []
        for line in output.split('\n'):
            if line.startswith('worktree ') and 'wt-' in line:
                path = Path(line.replace('worktree ', ''))
                if path.exists():
                    worktrees.append(path)
        return worktrees


class LogAnalyzer:
    """Analyze logs to diagnose issues and suggest improvements."""

    ERROR_PATTERNS = [
        (r'ERROR.*?:(.+)', 'error'),
        (r'Exception:(.+)', 'exception'),
        (r'Traceback', 'traceback'),
        (r'Failed to(.+)', 'failure'),
        (r'Timeout', 'timeout'),
        (r'CONFLICT', 'conflict'),
    ]

    def __init__(self, logs_path: Path, claude_cmd: str):
        self.logs_path = logs_path
        self.claude_cmd = claude_cmd
        self.issues_file = logs_path / 'issues.json'
        self.improvements_file = logs_path / 'self_improvements.json'

    def get_recent_logs(self, lines: int = 100) -> str:
        """Get recent log entries.

        Args:
            lines: Number of recent lines to retrieve (default: 100)

        Returns:
            String containing recent log lines, empty string if error
        """
        log_file = self.logs_path / 'runner.log'

        # Handle missing log file
        if not log_file.exists():
            logger.debug(f"Log file not found: {log_file}")
            return ""

        # Handle empty log file
        try:
            if log_file.stat().st_size == 0:
                logger.debug(f"Log file is empty: {log_file}")
                return ""
        except OSError as e:
            logger.warning(f"Cannot stat log file: {e}")
            return ""

        # Read log content with error handling
        try:
            content = log_file.read_text(encoding='utf-8', errors='replace')
            log_lines = content.split('\n')
            return '\n'.join(log_lines[-lines:])
        except PermissionError:
            logger.error(f"Permission denied reading log file: {log_file}")
            return ""
        except OSError as e:
            logger.error(f"Error reading log file: {e}")
            return ""
        except Exception as e:
            logger.error(f"Unexpected error reading logs: {e}")
            return ""

    def analyze_logs(self) -> Dict:
        """Analyze logs for errors and patterns."""
        logs = self.get_recent_logs(200)
        issues = []

        for pattern, issue_type in self.ERROR_PATTERNS:
            matches = re.findall(pattern, logs, re.IGNORECASE)
            for match in matches:
                issues.append({
                    'type': issue_type,
                    'detail': match if isinstance(match, str) else str(match),
                    'timestamp': datetime.now().isoformat()
                })

        return {
            'issues_found': len(issues),
            'issues': issues[-10:],  # Keep last 10
            'log_lines': len(logs.split('\n'))
        }

    def diagnose_and_fix(self, issue: Dict, repo_path: Path) -> Optional[str]:
        """Use Claude to diagnose and fix an issue."""
        prompt = f'''Analyze this issue from the SelfAI system and suggest a fix.

Issue Type: {issue.get('type')}
Detail: {issue.get('detail')}

Recent logs context:
{self.get_recent_logs(50)}

Repository: {repo_path}

TASK:
1. Understand what went wrong
2. Identify the root cause
3. If it's a code issue, fix it
4. If it's a configuration issue, adjust it
5. Report what you did

Be concise and actionable.'''

        try:
            result = subprocess.run(
                [self.claude_cmd, '-p', prompt, '--allowedTools',
                 'Read', 'Edit', 'Grep', 'Glob'],
                capture_output=True, text=True, timeout=300, cwd=str(repo_path)
            )
            if result.returncode == 0:
                return result.stdout
        except Exception as e:
            logger.warning(f"Diagnosis failed: {e}")
        return None

    def think_about_improvements(self, stats: Dict, repo_path: Path) -> List[Dict]:
        """Analyze performance and suggest self-improvements."""
        logs = self.get_recent_logs(500)
        analysis = self.analyze_logs()

        prompt = f'''You are the SelfAI system analyzing your own performance.

CURRENT STATS:
- Completed: {stats.get('completed', 0)}
- Pending: {stats.get('pending', 0)}
- Failed/Retried: {analysis.get('issues_found', 0)} issues

RECENT PATTERNS IN LOGS:
{logs[-2000:] if len(logs) > 2000 else logs}

TASK:
Based on your performance, suggest 2-3 specific improvements to make yourself better:
1. What patterns do you see in failures?
2. What could be optimized?
3. What new capabilities would help?

OUTPUT FORMAT (JSON):
```json
{{
  "self_improvements": [
    {{
      "title": "Improvement title",
      "description": "What to improve and why",
      "priority": 1-100
    }}
  ]
}}
```'''

        try:
            result = subprocess.run(
                [self.claude_cmd, '-p', prompt, '--allowedTools', 'Read', 'Glob'],
                capture_output=True, text=True, timeout=300, cwd=str(repo_path)
            )
            if result.returncode == 0:
                # Parse improvements
                output = result.stdout
                start = output.find('```json')
                end = output.find('```', start + 7)
                if start != -1 and end != -1:
                    json_str = output[start + 7:end].strip()
                    data = json.loads(json_str)
                    return data.get('self_improvements', [])
        except Exception as e:
            logger.warning(f"Self-improvement analysis failed: {e}")
        return []

    def save_issues(self, issues: List[Dict]):
        """Save issues to file for tracking."""
        try:
            existing = []
            if self.issues_file.exists():
                existing = json.loads(self.issues_file.read_text())
            existing.extend(issues)
            # Keep last 100 issues
            self.issues_file.write_text(json.dumps(existing[-100:], indent=2))
        except Exception:
            pass

    def save_improvements(self, improvements: List[Dict]):
        """Save self-improvement suggestions."""
        try:
            existing = []
            if self.improvements_file.exists():
                existing = json.loads(self.improvements_file.read_text())
            existing.extend(improvements)
            self.improvements_file.write_text(json.dumps(existing[-50:], indent=2))
        except Exception:
            pass


class Runner:
    """Autonomous self-improving runner with 3-level feature progression.

    Features:
    - Parallel task processing with git worktrees (isolated branches)
    - Each feature developed in its own branch
    - Automatic merge to main after tests pass
    - Conflict resolution with Claude assistance
    - Up to 5 parallel tasks supported
    """

    CLAUDE_CMD = 'claude'
    LOCK_FILE = 'selfai.lock'

    # Parallel processing config
    MAX_WORKERS = 5  # Max concurrent tasks (using worktrees)
    MAX_TASKS_PER_RUN = 5  # Max tasks to process in one run
    RUN_TIMEOUT = 600  # Max seconds per run cycle (10 min)

    # Level-specific guidance for plans
    # System context for all prompts
    SYSTEM_CONTEXT = """You are an AI agent working on SelfAI - an autonomous self-improving system.
This system runs as a LaunchAgent every 3 minutes, progressively implementing features through 3 levels:
MVP → Enhanced → Advanced. You are modifying the selfai/ Python module.

KEY RULES:
- Make REAL, WORKING changes - not placeholder code
- Test your changes mentally before committing
- Keep changes focused on the specific feature
- Don't break existing functionality
- Follow Python best practices"""

    LEVEL_GUIDANCE = {
        1: """MVP LEVEL - Minimal Viable Implementation:
GOAL: Get basic functionality working quickly

DO:
- Implement core feature with minimal code
- Add basic error handling (try/except for critical paths)
- Make it work for the happy path
- Use existing patterns in the codebase

DON'T:
- Over-engineer or add unnecessary abstractions
- Handle every edge case
- Add extensive documentation
- Create new files unless absolutely necessary

SUCCESS = Feature works for basic use case""",

        2: """ENHANCED LEVEL - Robust Implementation:
GOAL: Make the MVP production-worthy

DO:
- Add input validation for all public methods
- Handle edge cases (empty inputs, None values, invalid types)
- Improve error messages with actionable details
- Add type hints to modified functions
- Write focused unit tests

DON'T:
- Rewrite the entire feature
- Add features beyond the original scope
- Create complex abstractions

SUCCESS = Feature handles unexpected inputs gracefully""",

        3: """ADVANCED LEVEL - Production Excellence:
GOAL: Make it bulletproof and maintainable

DO:
- Optimize performance bottlenecks
- Add comprehensive logging
- Implement retry logic where appropriate
- Add docstrings to public APIs
- Ensure thread-safety if applicable
- Add integration tests

DON'T:
- Make breaking API changes
- Over-optimize prematurely

SUCCESS = Feature is production-ready with full coverage"""
    }

    def __init__(self, repo_path: Path):
        self.repo_path = repo_path
        self.workspace_path = repo_path / '.selfai_data'
        self.data_path = self.workspace_path / 'data'
        self.logs_path = self.workspace_path / 'logs'
        self.lock_path = self.data_path / self.LOCK_FILE

        self.data_path.mkdir(parents=True, exist_ok=True)
        self.logs_path.mkdir(parents=True, exist_ok=True)

        self.db = Database(self.data_path / 'improvements.db')
        self.worktree_mgr = WorktreeManager(repo_path, self.workspace_path)
        self.log_analyzer = LogAnalyzer(self.logs_path, self.CLAUDE_CMD)
        self._setup_logging()
        self._ensure_git_repo()

    def _setup_logging(self):
        """Setup file logging."""
        handler = logging.FileHandler(self.logs_path / 'runner.log')
        handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s'))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

    def _ensure_git_repo(self):
        """Ensure the repository is a git repo with main branch."""
        git_dir = self.repo_path / '.git'
        if not git_dir.exists():
            logger.warning("Not a git repository - worktrees disabled")
            return

        # Ensure we're on main branch
        result = subprocess.run(
            ['git', 'branch', '--show-current'],
            capture_output=True, text=True, cwd=str(self.repo_path)
        )
        current_branch = result.stdout.strip()

        if current_branch != 'main':
            # Try to checkout or create main
            subprocess.run(
                ['git', 'checkout', '-B', 'main'],
                capture_output=True, cwd=str(self.repo_path)
            )

    def acquire_lock(self) -> bool:
        """Acquire process lock."""
        if self.lock_path.exists():
            try:
                pid = int(self.lock_path.read_text().strip())
                os.kill(pid, 0)
                logger.info("Another instance is running, skipping")
                return False
            except (ProcessLookupError, ValueError):
                pass
        self.lock_path.write_text(str(os.getpid()))
        return True

    def release_lock(self):
        """Release process lock."""
        if self.lock_path.exists():
            self.lock_path.unlink()

    def get_status(self) -> Dict:
        """Get current status."""
        return self.db.get_stats()

    def run_once(self):
        """Run improvement cycle with parallel worktrees.

        Each task runs in its own git worktree (isolated branch):
        1. If no features exist, analyze existing codebase first
        2. Batch test features in parallel worktrees
        3. Process pending improvements in parallel worktrees
        4. Merge successful features to main
        5. Discover NEW features after all existing are complete

        Up to 5 parallel tasks using git worktrees.
        """
        if not self.acquire_lock():
            return

        start_time = time.time()
        tasks_processed = 0

        try:
            # Log analysis BEFORE starting tasks
            pre_analysis = self.log_analyzer.analyze_logs()
            if pre_analysis['issues_found'] > 0:
                logger.info(f"Pre-run log check: {pre_analysis['issues_found']} issues found")
                self.log_analyzer.save_issues(pre_analysis['issues'])

            stats = self.db.get_stats()

            # Phase 0: If no features exist, analyze existing codebase first
            if stats.get('total', 0) == 0:
                logger.info("No features in database - analyzing existing codebase...")
                self._discover_existing_features()
                return

            # Phase 1: Resume ALL stuck in_progress tasks first (PRIORITY)
            in_progress_batch = self._get_all_in_progress()
            if in_progress_batch:
                logger.info(f"Resuming {len(in_progress_batch)} stuck in_progress tasks (priority)...")
                self._run_parallel_improvements(in_progress_batch[:self.MAX_WORKERS])
                tasks_processed += len(in_progress_batch[:self.MAX_WORKERS])

            # Phase 2: Batch test features in PARALLEL WORKTREES (only if capacity remains)
            remaining_capacity = self.MAX_WORKERS - tasks_processed
            if remaining_capacity > 0:
                testing_batch = self._get_batch_needs_testing(remaining_capacity)
                if testing_batch:
                    logger.info(f"Batch testing {len(testing_batch)} features in parallel worktrees...")
                    self._run_parallel_tests(testing_batch)
                    tasks_processed += len(testing_batch)

            # Phase 3: Process pending improvements ONLY IF no in_progress tasks remain
            if not in_progress_batch:
                remaining_capacity = self.MAX_WORKERS - tasks_processed
                if remaining_capacity > 0:
                    pending_batch = self._get_batch_pending(remaining_capacity)
                    if pending_batch:
                        logger.info(f"Processing {len(pending_batch)} features in parallel worktrees...")
                        self._run_parallel_improvements(pending_batch)
                        tasks_processed += len(pending_batch)

            # Phase 4: Check if current level is complete and progress to next
            stats = self.db.get_stats()
            if stats.get('pending', 0) == 0 and stats.get('testing', 0) == 0 and stats.get('in_progress', 0) == 0:
                # All features at current level are complete
                level_stats = self.db.get_level_stats()
                current_level = self._get_current_batch_level()

                if current_level < 3:
                    # Progress all features to next level
                    next_level = current_level + 1
                    level_names = {1: 'MVP', 2: 'Enhanced', 3: 'Advanced'}
                    logger.info(f"All features completed {level_names[current_level]} - progressing to {level_names[next_level]}...")
                    self._progress_all_to_next_level(next_level)
                else:
                    # All features completed all 3 levels - discover NEW features
                    logger.info("All features completed all 3 levels - discovering new improvements...")
                    self._run_discovery()

                    # Self-improvement thinking (after discovery)
                    self._think_about_self_improvement(stats)

            # Log analysis AFTER completing tasks
            post_analysis = self.log_analyzer.analyze_logs()
            if post_analysis['issues_found'] > pre_analysis['issues_found']:
                new_issues = post_analysis['issues_found'] - pre_analysis['issues_found']
                logger.warning(f"Post-run log check: {new_issues} new issues detected")
                self.log_analyzer.save_issues(post_analysis['issues'])

                # Self-diagnosis: Try to fix new issues automatically
                self._diagnose_and_fix_issues(post_analysis['issues'][-new_issues:])

            logger.info(f"Run completed: {tasks_processed} tasks in {self._format_duration(time.time() - start_time)}")

        finally:
            self.release_lock()
            self.update_dashboard()
            self._check_self_deploy()

    def _get_current_batch_level(self) -> int:
        """Determine the current batch level based on completed tests.

        Returns the highest level where ALL features have passed tests.
        """
        stats = self.db.get_level_stats()
        total = self.db.get_stats().get('total', 0)

        if total == 0:
            return 1

        # Check from highest to lowest
        for level in [3, 2, 1]:
            passed = stats.get(level, {}).get('passed', 0)
            if passed == total:
                return level

        # Default to level 1 (MVP)
        return 1

    def _progress_all_to_next_level(self, next_level: int):
        """Progress all completed features to the next level."""
        level_names = {1: 'MVP', 2: 'Enhanced', 3: 'Advanced'}
        logger.info(f"Progressing all features to {level_names[next_level]} level...")

        # Get all completed features and move them to next level
        count = self.db.progress_all_to_level(next_level)
        logger.info(f"Moved {count} features to {level_names[next_level]} (pending)")

    def _diagnose_and_fix_issues(self, issues: List[Dict]):
        """Attempt to automatically diagnose and fix detected issues.

        Uses Claude to analyze issues and apply fixes to the codebase.
        Only attempts to fix issues that are likely code-related.
        """
        if not issues:
            return

        # Only attempt to fix certain types of issues
        fixable_types = {'error', 'exception', 'failure'}

        for issue in issues[:3]:  # Limit to 3 fixes per run to avoid loops
            if issue.get('type') not in fixable_types:
                continue

            logger.info(f"Attempting to diagnose issue: {issue.get('type')} - {issue.get('detail', '')[:50]}")

            try:
                result = self.log_analyzer.diagnose_and_fix(issue, self.repo_path)
                if result:
                    logger.info(f"Self-diagnosis applied fix: {result[:100]}")
                else:
                    logger.info(f"Could not auto-fix issue: {issue.get('detail', '')[:50]}")
            except Exception as e:
                logger.warning(f"Self-diagnosis failed: {e}")

    def _think_about_self_improvement(self, stats: Dict):
        """Analyze performance and suggest improvements to the system itself.

        Uses Claude to analyze patterns and suggest enhancements that would
        make the SelfAI system more effective.
        """
        # Only run occasionally - every 5 completed features
        completed = stats.get('completed', 0)
        if completed == 0 or completed % 5 != 0:
            return

        logger.info("Self-improvement: Analyzing system performance...")

        try:
            improvements = self.log_analyzer.think_about_improvements(stats, self.repo_path)

            if improvements:
                logger.info(f"Self-improvement: Generated {len(improvements)} suggestions")
                self.log_analyzer.save_improvements(improvements)

                # Add improvements to the database as new features to implement
                for imp in improvements[:2]:  # Only add top 2 suggestions per cycle
                    title = f"[Self-Improvement] {imp.get('title', 'Unknown')}"
                    if not self.db.exists(title):
                        self.db.add(
                            title=title,
                            description=imp.get('description', ''),
                            category='self-improvement',
                            priority=imp.get('priority', 60),
                            source='self_analysis'
                        )
                        logger.info(f"Added self-improvement: {title}")
        except Exception as e:
            logger.warning(f"Self-improvement thinking failed: {e}")

    def _get_batch_needs_testing(self, max_count: int) -> List[Dict]:
        """Get batch of improvements that need testing (unique only)."""
        results = []
        seen_ids = set()
        for _ in range(max_count * 2):  # Check more to find unique
            imp = self.db.get_next_needs_testing()
            if not imp:
                break
            if imp['id'] not in seen_ids:
                seen_ids.add(imp['id'])
                results.append(imp)
            if len(results) >= max_count:
                break
        return results

    def _get_all_in_progress(self) -> List[Dict]:
        """Get ALL stuck in-progress tasks for resumption.

        Returns all tasks currently marked as in_progress, which need to be
        completed before starting new pending tasks.
        """
        results = []
        seen_ids = set()
        while True:
            imp = self.db.get_next_in_progress()
            if not imp:
                break
            if imp['id'] not in seen_ids:
                seen_ids.add(imp['id'])
                results.append(imp)
        return results

    def _get_batch_pending(self, max_count: int) -> List[Dict]:
        """Get batch of pending improvements."""
        if max_count <= 0:
            return []
        results = []
        seen_ids = set()
        for _ in range(max_count * 2):
            imp = self.db.get_next_pending()
            if not imp:
                break
            if imp['id'] not in seen_ids:
                seen_ids.add(imp['id'])
                results.append(imp)
                self.db.mark_in_progress(imp['id'])  # Mark to avoid re-selection
            if len(results) >= max_count:
                break
        return results

    def _run_parallel_tests(self, improvements: List[Dict]):
        """Run tests for multiple improvements in parallel worktrees."""
        if not improvements:
            return

        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
            futures = {
                executor.submit(self._test_in_worktree, imp): imp
                for imp in improvements
            }

            for future in as_completed(futures):
                imp = futures[future]
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"Test failed for {imp['title']}: {e}")

    def _run_parallel_improvements(self, improvements: List[Dict]):
        """Process multiple improvements in parallel worktrees."""
        if not improvements:
            return

        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
            futures = {
                executor.submit(self._process_improvement_in_worktree, imp): imp
                for imp in improvements
            }

            for future in as_completed(futures):
                imp = futures[future]
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"Processing failed for {imp['title']}: {e}")

    def _process_improvement_in_worktree(self, improvement: Dict):
        """Process improvement in its own worktree with enhanced error handling."""
        imp_id = improvement['id']
        title = improvement['title']

        try:
            # Validate improvement before processing
            self.worktree_mgr.validate_improvement(improvement)

            # Create worktree for this feature
            worktree_path = self.worktree_mgr.create_worktree(imp_id, title)

            if worktree_path:
                # Process in worktree
                self._process_improvement(improvement, work_dir=worktree_path)
            else:
                # This shouldn't happen with new error handling, but keep as fallback
                logger.warning(f"Worktree creation returned None for #{imp_id}, using main repo")
                self._process_improvement(improvement)

        except ValidationError as e:
            logger.error(f"Validation failed for feature #{imp_id}: {e}")
            self.db.mark_failed(imp_id, f"Validation error: {e}")
        except DiskSpaceError as e:
            logger.error(f"Insufficient disk space for feature #{imp_id}: {e}")
            self.db.mark_failed(imp_id, f"Disk space error: {e}")
        except WorktreeError as e:
            logger.error(f"Worktree error for feature #{imp_id}: {e}")
            # Fallback to main repo
            logger.info(f"Falling back to main repo for feature #{imp_id}")
            try:
                self._process_improvement(improvement)
            except Exception as fallback_error:
                logger.error(f"Fallback processing also failed: {fallback_error}")
                self.db.mark_failed(imp_id, f"Worktree and fallback failed: {e}, {fallback_error}")
        except Exception as e:
            logger.error(f"Unexpected error processing feature #{imp_id}: {e}")
            self.db.mark_failed(imp_id, f"Unexpected error: {e}")

    def _test_in_worktree(self, improvement: Dict):
        """Test improvement in worktree, merge to main if passes."""
        imp_id = improvement['id']
        title = improvement['title']
        level = improvement['current_level']
        level_col = LEVEL_NAMES[level].lower()

        # Run tests (in main repo for testing)
        self._run_tests(improvement)

        # Check if test passed - get fresh data from database
        updated = self.db.get_by_id(imp_id)
        if not updated:
            logger.warning(f"Could not find feature #{imp_id} in database after test")
            self.worktree_mgr.cleanup_worktree(imp_id)
            return

        test_status = updated.get(f'{level_col}_test_status', 'pending')
        is_completed = updated.get('status') == 'completed'

        if test_status == 'passed' or is_completed:
            # Try to merge to main
            success, msg = self.worktree_mgr.merge_to_main(imp_id, title)
            if success:
                logger.info(f"✓ Merged feature #{imp_id} to main: {title}")
            elif 'CONFLICT' in msg:
                # Try to resolve conflicts
                logger.warning(f"Conflicts in #{imp_id}, attempting resolution...")
                if self.worktree_mgr.resolve_conflicts(self.CLAUDE_CMD, title):
                    logger.info(f"✓ Resolved conflicts and merged #{imp_id}")
                else:
                    logger.error(f"✗ Could not resolve conflicts for #{imp_id}")

        # Cleanup worktree
        self.worktree_mgr.cleanup_worktree(imp_id)

    def _run_tests(self, improvement: Dict):
        """Run tests for current level of improvement."""
        imp_id = improvement['id']
        title = improvement['title']
        level = improvement['current_level']
        level_name = LEVEL_NAMES[level]

        logger.info(f"Running {level_name} tests for: {title}")

        test_prompt = f'''{self.SYSTEM_CONTEXT}

=== TESTING AND FIXING: {title} ===
Level: {level_name} ({level}/3)
Repository: {self.repo_path}
Description: {improvement.get('description', '')}

TEST CRITERIA FOR {level_name}:
{self._get_test_criteria(level)}

=== YOUR MISSION ===

STEP 1 - DISCOVER: Find the implementation
- Use Glob to find relevant files (search for feature-related names)
- Read the main files: selfai/runner.py, selfai/database.py, selfai/__main__.py

STEP 2 - VERIFY: Check the implementation
- Verify imports are correct and modules exist
- Check for syntax errors (run: python -c "import selfai.runner")
- Trace the logic mentally - does it make sense?

STEP 3 - FIX: Repair any issues found
- Missing imports? ADD them
- Syntax errors? FIX them
- Logic bugs? REPAIR them
- Don't just report - ACTUALLY EDIT the files

STEP 4 - TEST: Run actual tests
- Execute: python -c "from selfai.runner import Runner; print('OK')"
- Run any pytest files if they exist
- Verify the feature works end-to-end

=== CRITICAL RULES ===
- You MUST fix issues, not just report them
- A feature only passes if it ACTUALLY WORKS
- When in doubt, test it by running Python code
- Be thorough - check all affected files

=== OUTPUT FORMAT ===
After completing all steps, output ONLY this JSON:
```json
{{
  "test_passed": true/false,
  "tests_run": ["what you verified"],
  "issues_fixed": ["what you fixed"],
  "remaining_issues": ["what you couldn't fix"]
}}
```'''

        result = self._execute_claude(test_prompt, timeout=300)

        if result['success']:
            output = result.get('output', '')
            passed = self._parse_test_result(output)

            if passed:
                self.db.mark_test_passed(imp_id, level, output)
                logger.info(f"✓ {level_name} PASSED - Feature completed: {title}")
            else:
                self.db.mark_test_failed(imp_id, level, output)
                logger.warning(f"✗ {level_name} FAILED for {title} - Will retry")
        else:
            self.db.mark_test_failed(imp_id, level, result.get('error', 'Test failed'))
            logger.error(f"Test execution failed for: {title}")

    def _get_test_criteria(self, level: int) -> str:
        """Get test criteria for each level."""
        criteria = {
            1: """MVP Test Criteria:
- Code runs without errors
- Basic functionality works
- No syntax errors
- Imports work correctly""",
            2: """Enhanced Test Criteria:
- All MVP criteria pass
- Edge cases handled
- Error messages are helpful
- Input validation works
- Tests cover main scenarios""",
            3: """Advanced Test Criteria:
- All Enhanced criteria pass
- Performance is acceptable
- Security considerations addressed
- Documentation is complete
- Full test coverage"""
        }
        return criteria.get(level, criteria[1])

    def _parse_test_result(self, output: str) -> bool:
        """Parse test output to determine pass/fail."""
        output_lower = output.lower()
        if '"test_passed": true' in output_lower or '"test_passed":true' in output_lower:
            return True
        if '"test_passed": false' in output_lower or '"test_passed":false' in output_lower:
            return False
        # Heuristic fallback
        fail_indicators = ['failed', 'error', 'exception', 'not working', 'broken']
        pass_indicators = ['passed', 'success', 'working', 'verified', 'complete']
        fail_count = sum(1 for i in fail_indicators if i in output_lower)
        pass_count = sum(1 for i in pass_indicators if i in output_lower)
        return pass_count > fail_count

    def _discover_existing_features(self):
        """Analyze codebase and catalog HIGH-LEVEL features (not functions)."""
        logger.info("Analyzing existing codebase for high-level features...")

        prompt = f'''Analyze this repository and identify HIGH-LEVEL FEATURES only.

Repository: {self.repo_path}

CRITICAL RULES:
1. List only HIGH-LEVEL FEATURES, NOT individual functions or methods
2. A feature is a complete user-facing capability or major component
3. Group related functionality into ONE feature (e.g., "Database Management" not "add_record", "delete_record", etc.)
4. Each feature should represent a significant, testable capability
5. Be selective - group related functions into single features

DO NOT LIST:
- Individual functions (like "mark_test_passed", "get_next_pending")
- Helper utilities (like "format_duration", "extract_json")
- Constants or configuration values
- Internal implementation details

GOOD EXAMPLES:
- "Autonomous Improvement Runner" (the main run cycle that orchestrates everything)
- "HTML Dashboard Generation" (creates visual progress tracking)
- "macOS LaunchAgent Integration" (scheduled background execution)
- "Git Worktree Parallel Processing" (isolated branch execution)

BAD EXAMPLES (too granular):
- "Mark Test Passed Function"
- "Get Next Pending Improvement"
- "Format Duration Helper"

OUTPUT FORMAT:
```json
{{
  "existing_features": [
    {{
      "title": "High-level feature name",
      "description": "What this feature does as a whole, main files involved",
      "category": "core|cli|integration|monitoring",
      "priority": 1-100
    }}
  ]
}}
```

Remember: Focus on HIGH-LEVEL capabilities, NOT individual functions.'''

        result = self._execute_claude(prompt, timeout=600)
        if result['success']:
            self._parse_existing_features(result['output'])

    def _parse_existing_features(self, output: str):
        """Parse and add existing features to database (filters out function-level items)."""
        try:
            json_str = self._extract_json(output)
            if json_str:
                data = json.loads(json_str)
                features = data.get('existing_features', [])
                added = 0
                # Words that indicate this is a function, not a feature
                skip_words = ['function', 'method', 'helper', 'utility', 'get_', 'set_', 'mark_',
                              'parse_', 'extract_', 'format_', '_to_', 'is_', 'has_']
                for feat in features:
                    title = feat.get('title', '')
                    # Skip if too granular (likely a function name)
                    if not title or len(title) < 10:
                        continue
                    if any(skip in title.lower() for skip in skip_words):
                        continue
                    if not self.db.exists(title):
                        self.db.add(
                            title=title,
                            description=feat.get('description', ''),
                            category=feat.get('category', 'feature'),
                            priority=feat.get('priority', 50),
                            source='existing'
                        )
                        logger.info(f"Found feature: {title}")
                        added += 1
                logger.info(f"Added {added} high-level features to database")
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Failed to parse existing features: {e}")
            # Try to extract features with regex as fallback
            self._parse_features_fallback(output, source='existing')

    def _extract_json(self, output: str) -> Optional[str]:
        """Extract JSON from output, handling various formats."""
        import re
        # Try to find JSON in code blocks
        patterns = [
            r'```json\s*([\s\S]*?)\s*```',
            r'```\s*([\s\S]*?)\s*```',
            r'\{[\s\S]*"(?:existing_features|improvements)"[\s\S]*\}'
        ]
        for pattern in patterns:
            match = re.search(pattern, output)
            if match:
                json_str = match.group(1) if '```' in pattern else match.group(0)
                # Clean up common issues
                json_str = json_str.strip()
                # Try to fix truncated JSON
                if json_str.count('{') > json_str.count('}'):
                    json_str += '}' * (json_str.count('{') - json_str.count('}'))
                if json_str.count('[') > json_str.count(']'):
                    json_str += ']' * (json_str.count('[') - json_str.count(']'))
                try:
                    json.loads(json_str)
                    return json_str
                except json.JSONDecodeError:
                    continue
        return None

    def _parse_features_fallback(self, output: str, source: str = 'ai_discovered'):
        """Fallback parser using regex when JSON fails (filters out function-level items)."""
        import re
        # Words that indicate this is a function, not a feature
        skip_words = ['function', 'method', 'helper', 'utility', 'get_', 'set_', 'mark_',
                      'parse_', 'extract_', 'format_', '_to_', 'is_', 'has_']

        # Look for patterns like "title": "..."
        title_patterns = [
            r'"title":\s*"([^"]+)"',
        ]
        found = set()
        for pattern in title_patterns:
            matches = re.findall(pattern, output, re.MULTILINE)
            for title in matches:
                title = title.strip()
                # Skip if too short, too long, or looks like a function
                if len(title) < 15 or len(title) > 80:
                    continue
                if any(skip in title.lower() for skip in skip_words):
                    continue
                if title not in found and not self.db.exists(title):
                    self.db.add(
                        title=title,
                        description='Auto-discovered feature',
                        category='feature',
                        priority=50,
                        source=source
                    )
                    found.add(title)
                    logger.info(f"Fallback: Found feature: {title}")
        if found:
            logger.info(f"Fallback parser added {len(found)} features")

    def _run_discovery(self):
        """Discover NEW improvements from web research (after all levels complete)."""
        completed = self.db.get_completed_features()
        completed_context = "\n".join([f"  - {f}" for f in completed[-15:]]) if completed else "  None yet"

        # Read project description to understand what it does
        readme_content = ""
        readme_path = self.repo_path / "README.md"
        if readme_path.exists():
            readme_content = readme_path.read_text()[:2000]

        prompt = f'''You are researching NEW features to add to this project.

PROJECT: {self.repo_path.name}
{f"DESCRIPTION: {readme_content[:500]}" if readme_content else ""}

ALREADY IMPLEMENTED (DO NOT DUPLICATE):
{completed_context}

YOUR TASK:
1. Understand what this project does
2. Research (using web search if available) what similar projects have
3. Suggest HIGH-VALUE features that would make this project better
4. Focus on features that are IMPORTANT and PRACTICAL

REQUIREMENTS:
- Each feature must be UNIQUE (not duplicating existing)
- Each feature must be VALUABLE to users
- Features should be HIGH-LEVEL capabilities (not functions)
- Prioritize: security, reliability, usability, performance
- Be conservative - only suggest truly useful features

OUTPUT FORMAT:
```json
{{
  "improvements": [
    {{
      "title": "Clear high-level feature name",
      "description": "WHY this is valuable, WHAT it does, HOW it improves the project",
      "category": "feature|security|reliability|performance|usability",
      "priority": 1-100
    }}
  ]
}}
```

Remember: Quality over quantity. Only suggest features that truly matter.'''

        result = self._execute_claude(prompt, timeout=600)
        if result['success']:
            self._parse_discoveries(result['output'])

    def _parse_discoveries(self, output: str):
        """Parse and add discovered improvements."""
        try:
            json_str = self._extract_json(output)
            if json_str:
                data = json.loads(json_str)
                improvements = data.get('improvements', [])
                added = 0
                for imp in improvements:
                    title = imp.get('title', '')
                    if title and not self.db.exists(title):
                        self.db.add(
                            title=title,
                            description=imp.get('description', ''),
                            category=imp.get('category', 'feature'),
                            priority=imp.get('priority', 50)
                        )
                        logger.info(f"Added: {title}")
                        added += 1
                logger.info(f"Added {added} new improvements")
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Failed to parse discoveries: {e}")
            self._parse_features_fallback(output, source='ai_discovered')

    def _process_improvement(self, improvement: Dict, work_dir: Path = None):
        """Process improvement at its current level.

        Args:
            improvement: The improvement dict from database
            work_dir: Optional working directory (worktree path)
        """
        imp_id = improvement['id']
        title = improvement['title']
        level = improvement['current_level']
        level_name = LEVEL_NAMES[level]
        start_time = time.time()

        # Use worktree path if provided, else main repo
        exec_path = work_dir or self.repo_path

        logger.info(f"Processing {level_name}: {title} (in {exec_path.name})")
        self.db.mark_in_progress(imp_id)
        self.update_dashboard()

        # Get or create plan for this level
        plan = self.db.get_plan(imp_id, level)
        if not plan:
            plan = self._create_plan(improvement, level, exec_path)
            if not plan:
                self.db.mark_failed(imp_id, "Planning failed")
                return
            self.db.save_plan(imp_id, level, plan)

        # Execute the plan in the specified directory
        output = self._execute_plan(improvement, level, plan, exec_path)
        duration = time.time() - start_time

        if output:
            self.db.mark_level_completed(imp_id, level, output)
            logger.info(f"✓ {level_name} completed: {title} ({self._format_duration(duration)})")

            # If using worktree, commit changes
            if work_dir:
                self._commit_worktree_changes(work_dir, imp_id, title, level_name)
        else:
            self.db.mark_failed(imp_id, "Execution failed")
            logger.error(f"✗ {level_name} failed: {title}")

    def _commit_worktree_changes(self, work_dir: Path, imp_id: int, title: str, level_name: str):
        """Commit changes made in a worktree."""
        try:
            # Add all changes
            subprocess.run(['git', 'add', '-A'], cwd=str(work_dir), capture_output=True)

            # Commit
            commit_msg = f"[SelfAI] {level_name}: {title}\n\nFeature #{imp_id}"
            subprocess.run(
                ['git', 'commit', '-m', commit_msg],
                cwd=str(work_dir), capture_output=True
            )
            logger.info(f"Committed changes for #{imp_id} in worktree")
        except Exception as e:
            logger.warning(f"Failed to commit worktree changes: {e}")

    def _create_plan(self, improvement: Dict, level: int, work_dir: Path = None) -> Optional[str]:
        """Create execution plan for specific level."""
        title = improvement['title']
        level_name = LEVEL_NAMES[level]
        exec_path = work_dir or self.repo_path

        # Get previous level's output for context
        prev_context = ""
        if level > 1:
            prev_level_col = {2: 'mvp', 3: 'enhanced'}[level]
            prev_output = improvement.get(f'{prev_level_col}_output', '')
            if prev_output:
                prev_context = f"\nPREVIOUS LEVEL OUTPUT:\n{prev_output[:2000]}"

        prompt = f'''{self.SYSTEM_CONTEXT}

=== PLANNING: {title} ===
Level: {level_name} ({level}/3)
Repository: {exec_path}
Description: {improvement.get('description', '')}
{prev_context}

{self.LEVEL_GUIDANCE[level]}

=== YOUR TASK ===
Create a SPECIFIC, ACTIONABLE plan for this feature.

STEP 1: Analyze existing code
- Read selfai/runner.py, selfai/database.py to understand current structure
- Identify where this feature should be implemented
- Note any existing similar patterns to follow

STEP 2: Create detailed plan
- List EXACT files to modify
- Describe SPECIFIC code changes (not vague descriptions)
- Estimate lines of code to add/modify

=== OUTPUT FORMAT ===
## Analysis
- Current state: [what exists]
- Gap: [what's missing]
- Approach: [how to implement]

## Implementation Plan
1. [File: exact change description]
2. [File: exact change description]
...

## Code Snippets (key additions)
```python
# Key code you'll add
```

## Verification Steps
- How to test this works'''

        result = self._execute_claude(prompt, timeout=180)
        if result['success']:
            logger.info(f"Plan created for {level_name}: {title}")
            return result.get('output', '')
        logger.error(f"Planning failed for: {title}")
        return None

    def _execute_plan(self, improvement: Dict, level: int, plan: str, work_dir: Path = None) -> Optional[str]:
        """Execute the plan for specific level."""
        title = improvement['title']
        level_name = LEVEL_NAMES[level]
        exec_path = work_dir or self.repo_path

        prompt = f'''{self.SYSTEM_CONTEXT}

=== EXECUTING: {title} ===
Level: {level_name} ({level}/3)
Repository: {exec_path}

PLAN TO EXECUTE:
{plan}

=== EXECUTION RULES ===
1. Make REAL code changes - use Edit tool to modify files
2. Follow the plan step by step
3. After each change, verify it doesn't break imports
4. Keep changes minimal and focused
5. DO NOT create new files unless the plan explicitly requires it
6. DO NOT add comments explaining what you did

=== QUALITY CHECKS ===
Before finishing, verify:
- [ ] All imports are valid (no circular imports)
- [ ] Code syntax is correct
- [ ] Changes match the {level_name} level requirements
- [ ] No placeholder code (TODO, FIXME, pass)

=== OUTPUT ===
After making all changes, briefly summarize:
- Files modified
- Key changes made
- Any issues encountered

Execute the plan now.'''

        result = self._execute_claude(prompt, timeout=900, work_dir=exec_path)
        if result['success']:
            return result.get('output', '')
        return None

    def _execute_claude(self, prompt: str, timeout: int = 300, work_dir: Path = None) -> Dict:
        """Execute Claude CLI command in specified directory."""
        exec_path = work_dir or self.repo_path
        try:
            result = subprocess.run(
                [self.CLAUDE_CMD, '-p', prompt, '--allowedTools',
                 'Edit', 'Write', 'Bash', 'Glob', 'Grep', 'Read'],
                capture_output=True, text=True, timeout=timeout, cwd=str(exec_path)
            )
            if result.returncode != 0:
                logger.error(f"Claude CLI failed: {result.stderr[:500] if result.stderr else 'No error output'}")
            return {'success': result.returncode == 0, 'output': result.stdout, 'error': result.stderr}
        except subprocess.TimeoutExpired:
            logger.warning(f"Claude call timed out after {timeout}s")
            return {'success': False, 'error': f'Timeout after {timeout}s'}
        except FileNotFoundError:
            logger.error("Claude CLI not found - ensure 'claude' is in PATH")
            return {'success': False, 'error': 'Claude CLI not found'}
        except Exception as e:
            logger.error(f"Claude execution error: {e}")
            return {'success': False, 'error': str(e)}

    def _format_duration(self, seconds: float) -> str:
        """Format duration in human readable form."""
        if seconds < 60:
            return f"{int(seconds)}s"
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}m {secs}s"

    def update_dashboard(self):
        """Update HTML dashboard with parallel processing info."""
        stats = self.db.get_stats()
        level_stats = self.db.get_level_stats()
        improvements = self.db.get_tasks_with_time_estimates()

        # Get active worktrees for parallel task tracking
        active_worktrees = self.worktree_mgr.get_active_worktrees()

        html_content = self._generate_dashboard_html(improvements, stats, level_stats, active_worktrees)
        dashboard_path = self.workspace_path / 'dashboard.html'
        dashboard_path.write_text(html_content)
        logger.info(f"Dashboard updated: {stats.get('completed', 0)} completed, {stats.get('pending', 0)} pending, {len(active_worktrees)} parallel")

    def _get_level_progress_indicator(self, imp: dict, level: int) -> str:
        """Generate visual progress indicator for current level (Plan → Execute → Test)."""
        level_prefix = {1: 'mvp', 2: 'enhanced', 3: 'advanced'}.get(level, 'mvp')

        # Check what's completed at this level
        has_plan = imp.get(f'{level_prefix}_plan') is not None
        has_output = imp.get(f'{level_prefix}_output') is not None
        test_status = imp.get(f'{level_prefix}_test_status', 'pending')

        # Build progress indicator based on workflow stage
        # ○ = pending, ● = completed/in-progress, ✓ = passed, ✗ = failed
        plan_icon = '●' if has_plan else '○'
        exec_icon = '●' if has_output else '○'

        if test_status == 'passed':
            test_icon = '✓'
        elif test_status == 'failed':
            test_icon = '✗'
        else:
            test_icon = '○'

        return f'<span class="level-progress">{plan_icon} → {exec_icon} → {test_icon}</span>'

    def _generate_dashboard_html(self, improvements: list, stats: dict, level_stats: dict,
                                   active_worktrees: List[Path] = None) -> str:
        """Generate dashboard HTML with parallel task tracking."""
        active_worktrees = active_worktrees or []

        # Get IDs of active parallel tasks
        active_ids = set()
        for wt in active_worktrees:
            try:
                # Extract ID from worktree name (wt-{id})
                wt_id = int(wt.name.replace('wt-', ''))
                active_ids.add(wt_id)
            except (ValueError, AttributeError):
                pass

        rows = []
        for imp in improvements:
            status = imp['status']
            level = imp['current_level']
            level_name = LEVEL_NAMES.get(level, 'MVP')

            # Test status indicator - show which levels are tested
            mvp_test = imp.get('mvp_test_status', 'pending')
            enh_test = imp.get('enhanced_test_status', 'pending')
            adv_test = imp.get('advanced_test_status', 'pending')

            # Build progress indicator
            mvp_icon = '✓' if mvp_test == 'passed' else ('✗' if mvp_test == 'failed' else '○')
            enh_icon = '✓' if enh_test == 'passed' else ('✗' if enh_test == 'failed' else '–')
            adv_icon = '✓' if adv_test == 'passed' else ('✗' if adv_test == 'failed' else '–')
            progress = f"{mvp_icon} | {enh_icon} | {adv_icon}"

            # Completed level display
            completed_level = "–"
            if adv_test == 'passed':
                completed_level = "Advanced"
            elif enh_test == 'passed':
                completed_level = "Enhanced"
            elif mvp_test == 'passed':
                completed_level = "MVP"

            # Current level progress indicator (Plan → Execute → Test)
            level_progress = self._get_level_progress_indicator(imp, level)

            # Check if running in parallel worktree
            is_parallel = imp['id'] in active_ids
            parallel_indicator = '⚡' if is_parallel else ''

            # Estimated time remaining
            time_estimate = "–"
            if imp.get('estimated_remaining') is not None:
                time_estimate = self._format_duration(imp['estimated_remaining'])

            status_class = status.replace('_', '-')
            rows.append(f'''
            <tr class="{status_class}{' parallel' if is_parallel else ''}">
                <td>{imp['id']}</td>
                <td>{parallel_indicator} {html.escape(imp['title'])}</td>
                <td><span class="level-badge level-{level}">{level_name}</span> {level_progress}</td>
                <td class="progress-cell">{progress}</td>
                <td>{completed_level}</td>
                <td><span class="status-badge {status_class}">{status}</span></td>
                <td>{time_estimate}</td>
                <td>{imp['priority']}</td>
            </tr>''')

        return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SelfAI - Progress Tracker</title>
    <meta http-equiv="refresh" content="60">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            min-height: 100vh;
            padding: 40px 20px;
            color: #fff;
        }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        h1 {{
            text-align: center;
            margin-bottom: 10px;
            font-size: 2.5rem;
            background: linear-gradient(90deg, #00d4ff, #7b2cbf);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .subtitle {{ text-align: center; color: #888; margin-bottom: 30px; }}
        .stats {{
            display: flex;
            justify-content: center;
            gap: 20px;
            margin-bottom: 30px;
            flex-wrap: wrap;
        }}
        .stat-card {{
            background: rgba(255,255,255,0.1);
            padding: 20px 30px;
            border-radius: 12px;
            text-align: center;
        }}
        .stat-card .value {{ font-size: 2rem; font-weight: bold; }}
        .stat-card .label {{ color: #888; font-size: 0.9rem; }}
        .stat-card.pending .value {{ color: #eab308; }}
        .stat-card.testing .value {{ color: #3b82f6; }}
        .stat-card.completed .value {{ color: #22c55e; }}
        .stat-card.parallel .value {{ color: #a855f7; }}
        table {{
            width: 100%;
            border-collapse: collapse;
            background: rgba(255,255,255,0.05);
            border-radius: 12px;
            overflow: hidden;
        }}
        th {{
            background: rgba(255,255,255,0.1);
            padding: 15px;
            text-align: left;
            font-weight: 600;
        }}
        td {{ padding: 12px 15px; border-bottom: 1px solid rgba(255,255,255,0.05); }}
        .status-badge {{
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 0.8rem;
            font-weight: 600;
        }}
        .status-badge.pending {{ background: rgba(234,179,8,0.2); color: #eab308; }}
        .status-badge.in-progress {{ background: rgba(249,115,22,0.2); color: #f97316; }}
        .status-badge.testing {{ background: rgba(59,130,246,0.2); color: #3b82f6; }}
        .status-badge.completed {{ background: rgba(34,197,94,0.2); color: #22c55e; }}
        .level-badge {{
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 0.8rem;
            font-weight: 600;
        }}
        .level-badge.level-1 {{ background: rgba(34,197,94,0.2); color: #22c55e; }}
        .level-badge.level-2 {{ background: rgba(59,130,246,0.2); color: #3b82f6; }}
        .level-badge.level-3 {{ background: rgba(168,85,247,0.2); color: #a855f7; }}
        .level-progress {{
            font-size: 0.75rem;
            color: #888;
            font-family: monospace;
            margin-left: 8px;
            white-space: nowrap;
        }}
        .progress-cell {{ font-family: monospace; letter-spacing: 2px; }}
        tr.completed {{ opacity: 0.7; }}
        tr.parallel {{ background: rgba(168,85,247,0.1); animation: pulse 2s infinite; }}
        @keyframes pulse {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.7; }} }}
    </style>
</head>
<body>
    <div class="container">
        <h1>SelfAI</h1>
        <p class="subtitle">Parallel Git Worktrees | 3-Level Progressive Testing | Auto-Merge to Main</p>

        <div class="stats">
            <div class="stat-card pending">
                <div class="value">{stats.get('pending', 0)}</div>
                <div class="label">Pending</div>
            </div>
            <div class="stat-card testing">
                <div class="value">{stats.get('testing', 0) + stats.get('in_progress', 0)}</div>
                <div class="label">In Progress</div>
            </div>
            <div class="stat-card completed">
                <div class="value">{stats.get('completed', 0)}</div>
                <div class="label">Completed</div>
            </div>
            <div class="stat-card parallel">
                <div class="value">{len(active_worktrees)}</div>
                <div class="label">Parallel Workers</div>
            </div>
        </div>

        <table>
            <thead>
                <tr>
                    <th>ID</th>
                    <th>Feature</th>
                    <th>Working On</th>
                    <th>Tests (M|E|A)</th>
                    <th>Completed At</th>
                    <th>Status</th>
                    <th>Est. Time Remaining</th>
                    <th>Priority</th>
                </tr>
            </thead>
            <tbody>
                {''.join(rows) if rows else '<tr><td colspan="8" style="text-align:center;color:#888;">No improvements yet</td></tr>'}
            </tbody>
        </table>
    </div>
</body>
</html>'''

    def _check_self_deploy(self):
        """Check if all features are complete and deploy selfai → _selfai if so."""
        # Only run for selfai project itself
        if self.repo_path.name != 'selfai':
            return

        selfai_src = self.repo_path / 'selfai'
        selfai_dst = self.repo_path / '_selfai'

        # Check if selfai folder exists
        if not selfai_src.exists():
            return

        # Check if all features are completed (all 3 levels passed)
        stats = self.db.get_stats()
        if stats.get('total', 0) == 0:
            return

        pending = stats.get('pending', 0) + stats.get('in_progress', 0) + stats.get('testing', 0)
        if pending > 0:
            return  # Still have work to do

        # All completed! Deploy selfai → _selfai
        logger.info("🚀 All features completed! Deploying selfai → _selfai...")

        import shutil

        # Backup current _selfai (except __pycache__)
        backup_path = self.workspace_path / 'backups' / datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_path.mkdir(parents=True, exist_ok=True)

        for f in selfai_dst.glob('*.py'):
            shutil.copy(f, backup_path / f.name)

        # Copy new files from selfai to _selfai
        for f in selfai_src.glob('*.py'):
            shutil.copy(f, selfai_dst / f.name)
            logger.info(f"  Deployed: {f.name}")

        # Restart the LaunchAgent
        import subprocess
        label = f"com.selfai.{self.repo_path.name}"
        plist_path = Path.home() / 'Library' / 'LaunchAgents' / f'{label}.plist'

        if plist_path.exists():
            subprocess.run(['launchctl', 'unload', str(plist_path)], capture_output=True)
            subprocess.run(['launchctl', 'load', str(plist_path)], capture_output=True)
            logger.info("  LaunchAgent restarted!")

        logger.info("✓ Self-deployment complete! Running latest version.")
