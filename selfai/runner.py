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
from typing import Dict, Optional, List, Tuple, Union
from concurrent.futures import ThreadPoolExecutor, as_completed
from .database import Database, LEVEL_NAMES, MAX_TEST_RETRIES
from .exceptions import ValidationError

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

        # NOTE: Don't cleanup orphaned worktrees on startup - this causes race conditions
        # when multiple workers are running. Cleanup is done explicitly after task completion.

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

            # Force-create branch to handle race conditions atomically
            # First, try to create normally
            success, output = self._run_git('branch', branch_name, 'main', retry=False)

            if not success and 'already exists' in output:
                # Branch exists due to race condition - must cleanup worktree FIRST before deleting branch
                logger.info(f"Branch {branch_name} exists (race condition), force recreating...")

                # CRITICAL: Remove worktree BEFORE attempting to delete branch
                # Git refuses to delete branches that are checked out in worktrees
                # ALWAYS cleanup worktree - don't check if directory exists, as worktree
                # might be registered in Git even if directory is missing
                logger.debug(f"Cleaning up existing worktree before branch deletion (feature #{feature_id})")
                self.cleanup_worktree(feature_id, force=True)

                # Now delete branch with retry (ignore if already deleted)
                delete_success, delete_output = self._run_git('branch', '-D', branch_name)
                if not delete_success and 'not found' not in delete_output:
                    # Only fail if it's not a "branch not found" error
                    raise WorktreeError(
                        f"Failed to delete existing branch {branch_name}: {delete_output}"
                    )

                # Recreate immediately without retry to minimize race window
                success, output = self._run_git('branch', branch_name, 'main', retry=False)
                if not success:
                    raise WorktreeError(
                        f"Failed to recreate branch {branch_name} for feature #{feature_id}: {output}"
                    )
            elif not success:
                # Different error - fail
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

    def cleanup_worktree(self, feature_id: int, force: bool = False) -> None:
        """Remove a worktree and its branch with enhanced error handling.

        Args:
            feature_id: Feature ID to cleanup
            force: If True, force cleanup even if disabled by default

        Note:
            By default disabled to prevent race conditions, but can be forced
            when recreating worktrees to avoid branch deletion errors.
        """
        if not force:
            # DISABLED by default - Don't delete worktrees to prevent race conditions
            logger.debug(f"Worktree cleanup skipped for feature #{feature_id} (cleanup disabled)")
            return

        # Find ALL worktree paths for this feature (including collision-resolved ones)
        # by checking both Git's worktree list AND the filesystem
        worktree_paths_to_cleanup = []
        branch_name = None

        # 1. Check Git's worktree list for any wt-{feature_id} paths
        success, output = self._run_git('worktree', 'list', '--porcelain', retry=False)
        if success:
            lines = output.split('\n')
            i = 0
            while i < len(lines):
                line = lines[i]
                if line.startswith('worktree '):
                    wt_path_str = line.replace('worktree ', '').strip()
                    wt_path = Path(wt_path_str)
                    # Match wt-{feature_id} or wt-{feature_id}-*
                    if wt_path.name == f"wt-{feature_id}" or wt_path.name.startswith(f"wt-{feature_id}-"):
                        worktree_paths_to_cleanup.append(wt_path)
                        # Extract branch name from following lines
                        for j in range(i + 1, min(i + 5, len(lines))):
                            if lines[j].startswith('branch '):
                                branch_name = lines[j].replace('branch refs/heads/', '').strip()
                                break
                i += 1

        # 2. Also check filesystem for any wt-{feature_id}* directories
        if self.worktrees_path.exists():
            pattern = f"wt-{feature_id}*"
            for path in self.worktrees_path.glob(pattern):
                if path.is_dir() and path not in worktree_paths_to_cleanup:
                    worktree_paths_to_cleanup.append(path)

        if not worktree_paths_to_cleanup:
            logger.debug(f"No worktrees found for feature #{feature_id}, skipping cleanup")
            return

        try:
            # Remove all found worktrees
            for worktree_path in worktree_paths_to_cleanup:
                # Force remove directory FIRST (this is safer)
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

                # Remove worktree from git registry
                success, output = self._run_git('worktree', 'remove', str(worktree_path), '--force', retry=False)
                if not success:
                    logger.debug(f"Git worktree remove failed for {worktree_path.name}: {output}")

            # CRITICAL: Prune stale worktree entries from Git's registry
            # This removes worktree references even if 'git worktree remove' failed
            prune_success, prune_output = self._run_git('worktree', 'prune', retry=False)
            if not prune_success:
                logger.warning(f"Git worktree prune failed: {prune_output}")

            # Cleanup branch if found (now safe because worktree is pruned)
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

        Raises:
            ValidationError: If lines parameter is invalid
        """
        if not isinstance(lines, int):
            raise ValidationError(f"lines must be an integer, got {type(lines).__name__}")
        if lines <= 0:
            raise ValidationError(f"lines must be positive, got {lines}")
        if lines > 100000:
            raise ValidationError(f"lines too large (max 100000), got {lines}")

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
        except PermissionError as e:
            logger.error(f"Permission denied reading log file: {log_file}. Fix with: chmod 644 {log_file}")
            return ""
        except OSError as e:
            logger.error(f"Error reading log file: {e}. Check disk space and file system health.")
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
        """Use Claude to diagnose and fix an issue.

        Args:
            issue: Dict with 'type' and 'detail' keys
            repo_path: Path to repository

        Returns:
            Diagnosis output or None if failed

        Raises:
            ValidationError: If issue or repo_path is invalid
        """
        if not isinstance(issue, dict):
            raise ValidationError(f"issue must be a dict, got {type(issue).__name__}")
        if not issue:
            raise ValidationError("issue dict cannot be empty")
        if 'type' not in issue or 'detail' not in issue:
            raise ValidationError(f"issue must have 'type' and 'detail' keys, got {list(issue.keys())}")

        if not isinstance(repo_path, Path):
            raise ValidationError(f"repo_path must be a Path object, got {type(repo_path).__name__}")
        if not repo_path.exists():
            raise ValidationError(f"Repository path does not exist: {repo_path}")
        if not repo_path.is_dir():
            raise ValidationError(f"Repository path is not a directory: {repo_path}")

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
        except subprocess.TimeoutExpired:
            logger.warning(f"Diagnosis timed out after 300s for issue: {issue.get('type')}")
            return None
        except FileNotFoundError:
            logger.error(f"Claude CLI not found at: {self.claude_cmd}. Install it or update path.")
            return None
        except Exception as e:
            logger.warning(f"Diagnosis failed: {e}")
        return None

    def think_about_improvements(self, stats: Dict, repo_path: Path) -> List[Dict]:
        """Analyze performance and suggest self-improvements.

        Args:
            stats: Dict with performance statistics
            repo_path: Path to repository

        Returns:
            List of improvement suggestions

        Raises:
            ValidationError: If stats or repo_path is invalid
        """
        if stats is None:
            raise ValidationError("stats cannot be None")
        if not isinstance(stats, dict):
            raise ValidationError(f"stats must be a dict, got {type(stats).__name__}")

        if not isinstance(repo_path, Path):
            raise ValidationError(f"repo_path must be a Path object, got {type(repo_path).__name__}")
        if not repo_path.exists():
            raise ValidationError(f"Repository path does not exist: {repo_path}")

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

    def save_issues(self, issues: List[Dict]) -> None:
        """Save issues to file for tracking.

        Args:
            issues: List of issue dicts to save

        Raises:
            ValidationError: If issues is invalid
        """
        if issues is None:
            raise ValidationError("issues cannot be None")
        if not isinstance(issues, list):
            raise ValidationError(f"issues must be a list, got {type(issues).__name__}")

        try:
            existing = []
            if self.issues_file.exists():
                try:
                    content = self.issues_file.read_text()
                    if content.strip():
                        existing = json.loads(content)
                        if not isinstance(existing, list):
                            logger.warning(f"Existing issues file has invalid format, resetting")
                            existing = []
                except json.JSONDecodeError as e:
                    logger.warning(f"Corrupted issues file, resetting: {e}")
                    existing = []

            existing.extend(issues)
            self.issues_file.write_text(json.dumps(existing[-100:], indent=2))
        except PermissionError:
            logger.error(f"Permission denied writing to issues file: {self.issues_file}. Fix with: chmod 644 {self.issues_file}")
        except OSError as e:
            logger.error(f"Error writing issues file: {e}. Check disk space.")
        except Exception as e:
            logger.error(f"Unexpected error saving issues: {e}")

    def save_improvements(self, improvements: List[Dict]) -> None:
        """Save self-improvement suggestions.

        Args:
            improvements: List of improvement dicts to save

        Raises:
            ValidationError: If improvements is invalid
        """
        if improvements is None:
            raise ValidationError("improvements cannot be None")
        if not isinstance(improvements, list):
            raise ValidationError(f"improvements must be a list, got {type(improvements).__name__}")

        try:
            existing = []
            if self.improvements_file.exists():
                try:
                    content = self.improvements_file.read_text()
                    if content.strip():
                        existing = json.loads(content)
                        if not isinstance(existing, list):
                            logger.warning(f"Existing improvements file has invalid format, resetting")
                            existing = []
                except json.JSONDecodeError as e:
                    logger.warning(f"Corrupted improvements file, resetting: {e}")
                    existing = []

            existing.extend(improvements)
            self.improvements_file.write_text(json.dumps(existing[-50:], indent=2))
        except PermissionError:
            logger.error(f"Permission denied writing to improvements file: {self.improvements_file}. Fix with: chmod 644 {self.improvements_file}")
        except OSError as e:
            logger.error(f"Error writing improvements file: {e}. Check disk space.")
        except Exception as e:
            logger.error(f"Unexpected error saving improvements: {e}")


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
        # Always use absolute paths to avoid issues with working directory changes
        self.repo_path = repo_path.resolve()
        self.workspace_path = self.repo_path / '.selfai_data'
        self.data_path = self.workspace_path / 'data'
        self.logs_path = self.workspace_path / 'logs'
        self.lock_path = self.data_path / self.LOCK_FILE
        self.worktrees_path = self.workspace_path / 'worktrees'

        self.data_path.mkdir(parents=True, exist_ok=True)
        self.logs_path.mkdir(parents=True, exist_ok=True)

        self.db = Database(self.data_path / 'improvements.db')
        self.worktree_mgr = WorktreeManager(self.repo_path, self.workspace_path)
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

    def _validate_plan(self, plan: str, improvement: Dict) -> bool:
        """Validate plan structure and content.

        Args:
            plan: Plan content as string
            improvement: Improvement dict

        Returns:
            True if valid, False otherwise
        """
        from .validators import PlanValidator
        from .exceptions import PlanValidationError

        try:
            plan_dict = PlanValidator.validate_plan_structure(plan)
            PlanValidator.validate_file_paths(plan_dict, self.repo_path)
            PlanValidator.validate_dependencies(plan_dict)

            imp_id = improvement.get('id')
            level = improvement.get('current_level', 1)
            logger.info(f"Plan validation passed for improvement #{imp_id} at level {level}")
            return True

        except PlanValidationError as e:
            imp_id = improvement.get('id')
            logger.error(f"Plan validation failed for improvement #{imp_id}: {e}")
            self.db.mark_failed(imp_id, f"Invalid plan: {e}")
            return False

    def _check_file_conflicts(self, improvement: Dict, plan: str) -> bool:
        """Check for file conflicts with other in-progress improvements.

        Args:
            improvement: Current improvement
            plan: Plan containing files to modify

        Returns:
            True if no conflicts, False if conflicts detected
        """
        from .exceptions import WorktreeConflictError

        try:
            plan_dict = json.loads(plan)
            files_to_modify = plan_dict.get('files_to_modify', [])

            if not files_to_modify:
                return True

            conflicting = self.db.get_improvements_by_files(files_to_modify)

            if conflicting:
                imp_id = improvement.get('id')
                conflict_ids = [c['id'] for c in conflicting if c['id'] != imp_id]

                if conflict_ids:
                    logger.warning(
                        f"File conflict detected for improvement #{imp_id}. "
                        f"Conflicting improvements: {conflict_ids}. "
                        f"Files: {', '.join(files_to_modify[:3])}"
                    )
                    error_msg = (
                        f"File conflict with improvements {conflict_ids}. "
                        f"Waiting for them to complete."
                    )
                    self.db.update_status(imp_id, 'pending', error=error_msg)
                    return False

            return True

        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Cannot parse plan for conflict detection: {e}")
            return True

    def _get_execution_checkpoint(self, improvement: Dict) -> Optional[Dict]:
        """Get execution checkpoint for recovery.

        Args:
            improvement: Improvement dict

        Returns:
            Checkpoint data as dict or None
        """
        imp_id = improvement.get('id')
        checkpoint_str = self.db.get_execution_checkpoint(imp_id)

        if not checkpoint_str:
            return None

        try:
            return json.loads(checkpoint_str)
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid checkpoint data for improvement #{imp_id}: {e}")
            return None

    def _save_execution_checkpoint(self, improvement: Dict, checkpoint_data: Dict) -> None:
        """Save execution checkpoint for recovery.

        Args:
            improvement: Improvement dict
            checkpoint_data: Checkpoint data to save
        """
        imp_id = improvement.get('id')
        checkpoint_str = json.dumps(checkpoint_data)
        self.db.save_execution_checkpoint(imp_id, checkpoint_str)

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
        # Use direct database query to get ALL in_progress tasks at once
        # This avoids the infinite loop bug from repeatedly calling get_next_in_progress()
        return self.db.get_all_in_progress()

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
            # NOTE: Don't cleanup here - worktree will be reused or cleaned up on next create
            return

        test_status = updated.get(f'{level_col}_test_status', 'pending')
        is_completed = updated.get('status') == 'completed'

        if test_status == 'passed' or is_completed:
            # Try to merge to main
            success, msg = self.worktree_mgr.merge_to_main(imp_id, title)
            if success:
                logger.info(f"✓ Merged feature #{imp_id} to main: {title}")
                # Cleanup worktree only after successful merge
                self.worktree_mgr.cleanup_worktree(imp_id)
            elif 'CONFLICT' in msg:
                # Try to resolve conflicts
                logger.warning(f"Conflicts in #{imp_id}, attempting resolution...")
                if self.worktree_mgr.resolve_conflicts(self.CLAUDE_CMD, title):
                    logger.info(f"✓ Resolved conflicts and merged #{imp_id}")
                    # Cleanup after conflict resolution and merge
                    self.worktree_mgr.cleanup_worktree(imp_id)
                else:
                    logger.error(f"✗ Could not resolve conflicts for #{imp_id}")
                    # Keep worktree for manual inspection
        else:
            # Test failed or pending - keep worktree for debugging
            logger.debug(f"Keeping worktree for #{imp_id} (test status: {test_status})")

    def _quick_syntax_check(self) -> bool:
        """Quick syntax validation before full test - saves time on obvious errors."""
        try:
            result = subprocess.run(
                ['python', '-c', 'import selfai.runner; import selfai.database'],
                capture_output=True, text=True, timeout=10, cwd=str(self.repo_path)
            )
            return result.returncode == 0
        except Exception:
            return False

    def _validate_test_environment(self, improvement: Dict) -> tuple[bool, str]:
        """Validate that environment is ready for testing with comprehensive checks.

        Returns:
            (success, error_message) tuple
        """
        imp_id = improvement['id']
        title = improvement.get('title', f'#{imp_id}')

        retry_count = improvement.get('retry_count', 0)
        if retry_count >= MAX_TEST_RETRIES:
            return False, f"Feature has exceeded maximum test retries ({MAX_TEST_RETRIES})"

        worktree_path = self.worktrees_path / f"wt-{imp_id}"

        if not worktree_path.exists():
            pattern = f"wt-{imp_id}-*"
            matches = list(self.worktrees_path.glob(pattern))
            if matches:
                worktree_path = matches[0]
            else:
                return False, f"Worktree not found for feature #{imp_id}. Implementation may not have started."

        if not (worktree_path / 'selfai').exists():
            return False, f"Worktree missing selfai module: {worktree_path}. Worktree may be corrupted."

        required_files = ['selfai/__init__.py', 'selfai/runner.py', 'selfai/database.py']
        for req_file in required_files:
            file_path = worktree_path / req_file
            if not file_path.exists():
                return False, f"Required file missing in worktree: {req_file}"

        if not self.worktrees_path.exists():
            return False, f"Worktrees directory not found: {self.worktrees_path}"

        return True, ""

    def _run_tests(self, improvement: Dict):
        """Run tests for current level of improvement with comprehensive error handling."""
        imp_id = improvement['id']
        title = improvement['title']
        level = improvement['current_level']
        level_name = LEVEL_NAMES[level]

        level_col = LEVEL_NAMES[level].lower()
        current_status = improvement.get(f'{level_col}_test_status')
        if current_status == 'passed':
            logger.info(f"Skipping {level_name} tests for {title} - already passed")
            return

        valid, error_msg = self._validate_test_environment(improvement)
        if not valid:
            logger.error(f"Test environment validation failed: {error_msg}")
            self.db.mark_test_failed(imp_id, level, f"Environment validation failed: {error_msg}")
            return

        if not self._quick_syntax_check():
            logger.warning(f"Quick syntax check failed for {title} - fixing before full test")

        test_timeout = {1: 180, 2: 240, 3: 300}[level]

        logger.info(f"Running {level_name} tests for: {title} (timeout: {test_timeout}s)")

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

        try:
            result = self._execute_claude(test_prompt, timeout=test_timeout)

            if result['success']:
                output = result.get('output', '')
                if not output:
                    logger.warning(f"Empty output from test execution for {title}")
                    self.db.mark_test_failed(imp_id, level, "Empty test output - Claude returned no response")
                    return

                if len(output) < 50:
                    logger.warning(f"Suspiciously short test output ({len(output)} chars) for {title}")

                passed = self._parse_test_result(output)

                if passed:
                    self.db.mark_test_passed(imp_id, level, output[:5000])
                    logger.info(f"✓ {level_name} PASSED - Feature completed: {title}")
                else:
                    retry_count = improvement.get('retry_count', 0)
                    if retry_count == 0:
                        logger.info(f"First failure for {title} - will retry with fixes")
                    elif retry_count == MAX_TEST_RETRIES - 1:
                        logger.warning(f"Final retry attempt for {title} - will be marked permanently failed if this fails")
                    self.db.mark_test_failed(imp_id, level, output[:5000])
                    logger.warning(f"✗ {level_name} FAILED for {title} - Will retry (attempt {retry_count + 1}/{MAX_TEST_RETRIES})")
            else:
                error_msg = result.get('error', 'Test execution failed')
                if 'api' in error_msg.lower() or 'anthropic' in error_msg.lower():
                    logger.error(f"Claude API error for: {title} - {error_msg}")
                    self.db.mark_test_failed(imp_id, level, f"Claude API error: {error_msg}"[:5000])
                else:
                    logger.error(f"Test execution error for: {title} - {error_msg}")
                    self.db.mark_test_failed(imp_id, level, f"Execution error: {error_msg}"[:5000])

        except subprocess.TimeoutExpired:
            logger.error(f"Test execution timed out after {test_timeout}s for: {title}")
            timeout_msg = f"Test timed out after {test_timeout}s. Consider breaking feature into smaller pieces or increasing timeout for {level_name} level."
            self.db.mark_test_failed(imp_id, level, timeout_msg)
        except KeyboardInterrupt:
            logger.warning(f"Test interrupted by user for: {title}")
            self.db.mark_test_failed(imp_id, level, "Test interrupted by user")
            raise
        except Exception as e:
            error_type = type(e).__name__
            logger.error(f"Unexpected {error_type} during test execution for {title}: {e}")
            self.db.mark_test_failed(imp_id, level, f"Unexpected {error_type}: {str(e)}"[:5000])

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
        """Parse test output to determine pass/fail with validation and confidence scoring."""
        if not output or not output.strip():
            logger.warning("Empty test output - marking as failed")
            return False

        try:
            output_lower = output.lower()
        except (AttributeError, UnicodeDecodeError) as e:
            logger.error(f"Failed to process test output (encoding issue): {e}")
            return False

        try:
            start = output.find('```json')
            end = output.find('```', start + 7) if start != -1 else -1

            if start != -1 and end != -1:
                json_str = output[start + 7:end].strip()
                if not json_str:
                    logger.warning("Empty JSON block found in test output")
                elif len(json_str) > 50000:
                    logger.warning(f"JSON block too large ({len(json_str)} chars), likely invalid")
                else:
                    try:
                        data = json.loads(json_str)
                        if not isinstance(data, dict):
                            logger.warning(f"JSON is not a dict: {type(data)}")
                        elif 'test_passed' not in data:
                            logger.warning("JSON missing required 'test_passed' field")
                        else:
                            result = data['test_passed']
                            if isinstance(result, bool):
                                logger.info(f"Parsed test result from JSON: {result}")
                                return result
                            elif isinstance(result, str):
                                if result.lower() in ('true', 'yes', '1'):
                                    logger.info("Converted string 'true' to boolean True")
                                    return True
                                elif result.lower() in ('false', 'no', '0'):
                                    logger.info("Converted string 'false' to boolean False")
                                    return False
                                else:
                                    logger.warning(f"test_passed string value unrecognized: {result}")
                            else:
                                logger.warning(f"test_passed is not boolean: {type(result)}")
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse JSON in test output: {e}")
        except Exception as e:
            logger.warning(f"Error extracting JSON from test output: {e}")

        if '"test_passed": true' in output_lower or '"test_passed":true' in output_lower:
            logger.info("Found test_passed=true via string search")
            return True
        if '"test_passed": false' in output_lower or '"test_passed":false' in output_lower:
            logger.info("Found test_passed=false via string search")
            return False

        fail_indicators = ['failed', 'error', 'exception', 'not working', 'broken', 'traceback', 'importerror', 'syntaxerror']
        pass_indicators = ['passed', 'success', 'working', 'verified', 'complete', 'ok', 'all tests', 'no errors']

        fail_count = sum(1 for indicator in fail_indicators if indicator in output_lower)
        pass_count = sum(1 for indicator in pass_indicators if indicator in output_lower)

        confidence = abs(pass_count - fail_count)
        total_indicators = pass_count + fail_count

        if total_indicators == 0:
            logger.warning("No test indicators found in output - assuming failed")
            return False

        result = pass_count > fail_count
        confidence_pct = (confidence / total_indicators * 100) if total_indicators > 0 else 0

        logger.info(f"Heuristic parse: pass={pass_count}, fail={fail_count}, confidence={confidence_pct:.1f}%, result={result}")

        return result

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

            if not self._validate_plan(plan, improvement):
                logger.error(f"Plan validation failed for improvement #{imp_id}")
                return

            self.db.save_plan(imp_id, level, plan)

        if not self._check_file_conflicts(improvement, plan):
            logger.warning(f"File conflicts detected for improvement #{imp_id}, deferring")
            return

        # Execute the plan in the specified directory
        output = self._execute_plan(improvement, level, plan, exec_path)
        duration = time.time() - start_time

        if output:
            # If using worktree, commit changes BEFORE marking completed
            if work_dir:
                self._commit_worktree_changes(work_dir, imp_id, title, level_name)

            self.db.mark_level_completed(imp_id, level, output)
            logger.info(f"✓ {level_name} completed: {title} ({self._format_duration(duration)})")
        else:
            self.db.mark_failed(imp_id, "Execution failed")
            logger.error(f"✗ {level_name} failed: {title}")

    def _commit_worktree_changes(self, work_dir: Path, imp_id: int, title: str, level_name: str):
        """Commit changes made in a worktree.

        Uses a fallback strategy: tries to commit in worktree first,
        but if worktree is gone, commits via main repo checkout.
        """
        branch_name = self.worktree_mgr._sanitize_branch_name(f"{imp_id}-{title}")
        commit_msg = f"[SelfAI] {level_name}: {title}\n\nFeature #{imp_id}"

        # Strategy 1: Try to commit in worktree (preferred - faster)
        try:
            if work_dir.exists():
                # Check if there are changes to commit
                status_result = subprocess.run(
                    ['git', 'status', '--porcelain'],
                    cwd=str(work_dir),
                    capture_output=True,
                    timeout=10,
                    text=True
                )

                if not status_result.stdout.strip():
                    logger.debug(f"No changes to commit for #{imp_id}")
                    return

                # Add all changes
                subprocess.run(
                    ['git', 'add', '-A'],
                    cwd=str(work_dir),
                    capture_output=True,
                    timeout=30,
                    check=True
                )

                # Commit
                subprocess.run(
                    ['git', 'commit', '-m', commit_msg],
                    cwd=str(work_dir),
                    capture_output=True,
                    timeout=30,
                    check=True
                )

                logger.info(f"Committed changes for #{imp_id} in worktree")
                return

        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.warning(f"Worktree commit failed for #{imp_id}: {e}, trying fallback...")

        # Strategy 2: Fallback - commit via main repo checkout
        try:
            logger.info(f"Using fallback: committing #{imp_id} via main repo checkout")

            # Stash any changes in main repo
            subprocess.run(
                ['git', 'stash', 'push', '-m', f'Auto-stash before #{imp_id} commit'],
                cwd=str(self.worktree_mgr.repo_path),
                capture_output=True,
                timeout=30
            )

            # Checkout the feature branch
            result = subprocess.run(
                ['git', 'checkout', branch_name],
                cwd=str(self.worktree_mgr.repo_path),
                capture_output=True,
                timeout=30
            )

            if result.returncode != 0:
                logger.warning(f"Could not checkout branch {branch_name}: {result.stderr.decode()}")
                return

            # Add and commit changes
            subprocess.run(
                ['git', 'add', '-A'],
                cwd=str(self.worktree_mgr.repo_path),
                capture_output=True,
                timeout=30,
                check=True
            )

            subprocess.run(
                ['git', 'commit', '-m', commit_msg, '--allow-empty'],
                cwd=str(self.worktree_mgr.repo_path),
                capture_output=True,
                timeout=30,
                check=True
            )

            logger.info(f"Committed changes for #{imp_id} via fallback method")

            # Return to main branch
            subprocess.run(
                ['git', 'checkout', 'main'],
                cwd=str(self.worktree_mgr.repo_path),
                capture_output=True,
                timeout=30
            )

            # Restore stashed changes
            subprocess.run(
                ['git', 'stash', 'pop'],
                cwd=str(self.worktree_mgr.repo_path),
                capture_output=True,
                timeout=30
            )

        except Exception as e:
            logger.error(f"Both worktree and fallback commit failed for #{imp_id}: {e}")

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
Output your plan as a JSON object with the following structure:
```json
{{
  "description": "Brief summary of what will be implemented",
  "files_to_modify": ["path/to/file1.py", "path/to/file2.py"],
  "analysis": {{
    "current_state": "what exists",
    "gap": "what's missing",
    "approach": "how to implement"
  }},
  "implementation_steps": [
    "Step 1: specific action",
    "Step 2: specific action"
  ],
  "verification": "How to test this works"
}}
```

IMPORTANT: Output ONLY the JSON object, nothing else.'''

        # OPTIMIZATION: Level-based planning timeout (MVP=120s, Enhanced=150s, Advanced=180s)
        plan_timeout = {1: 120, 2: 150, 3: 180}[level]
        result = self._execute_claude(prompt, timeout=plan_timeout)
        if result['success']:
            logger.info(f"Plan created for {level_name}: {title}")
            output = result.get('output', '')
            # Extract JSON from markdown code fence if present
            output = self._extract_json_from_output(output)
            return output
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

        # OPTIMIZATION: Level-based execution timeout (MVP=300s, Enhanced=600s, Advanced=900s)
        exec_timeout = {1: 300, 2: 600, 3: 900}[level]
        result = self._execute_claude(prompt, timeout=exec_timeout, work_dir=exec_path)
        if result['success']:
            return result.get('output', '')
        return None

    def _extract_json_from_output(self, output: str) -> str:
        """Extract JSON from Claude CLI output.

        Claude CLI may wrap JSON in markdown code fences or include extra text.
        This method extracts just the JSON content.

        Args:
            output: Raw output from Claude CLI

        Returns:
            Cleaned JSON string
        """
        if not output:
            return output

        # Try to find JSON in markdown code fence
        json_pattern = r'```(?:json)?\s*\n(.*?)\n```'
        match = re.search(json_pattern, output, re.DOTALL)
        if match:
            return match.group(1).strip()

        # If no code fence, try to find JSON object directly
        # Look for content between first { and last }
        first_brace = output.find('{')
        last_brace = output.rfind('}')
        if first_brace != -1 and last_brace != -1 and first_brace < last_brace:
            return output[first_brace:last_brace + 1].strip()

        # Return as-is if no JSON structure found
        return output.strip()

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

    def _format_duration(self, seconds: Optional[Union[int, float]]) -> str:
        """Format duration in human readable form.

        Args:
            seconds: Duration in seconds (int or float), can be None

        Returns:
            Formatted string like "1h 5m 30s" or "5m 30s" or "30s" or "–" if invalid
        """
        if seconds is None:
            return "–"

        try:
            seconds = float(seconds)
            if seconds < 0:
                return "–"
            if seconds < 60:
                return f"{int(seconds)}s"

            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            secs = int(seconds % 60)

            if hours > 0:
                return f"{hours}h {minutes}m {secs}s"
            else:
                return f"{minutes}m {secs}s"
        except (TypeError, ValueError) as e:
            logger.warning(f"Invalid duration value: {seconds}, error: {e}")
            return "–"

    def update_dashboard(self) -> None:
        """Update HTML dashboard with parallel processing info.

        Handles errors gracefully and uses fallback values if database queries fail.
        """
        try:
            stats = self.db.get_stats()
            if stats is None:
                logger.error("Failed to get stats from database, using empty stats")
                stats = {'pending': 0, 'testing': 0, 'in_progress': 0, 'completed': 0, 'total': 0}

            level_stats = self.db.get_level_stats()
            if level_stats is None:
                logger.warning("Failed to get level stats, using empty dict")
                level_stats = {}

            success_fail_stats = self.db.get_success_fail_stats()
            if success_fail_stats is None:
                logger.warning("Failed to get success/fail stats, using empty dict")
                success_fail_stats = {}

            improvements = self.db.get_tasks_with_time_estimates()
            if improvements is None:
                logger.warning("Failed to get improvements, using empty list")
                improvements = []

            active_worktrees = self.worktree_mgr.get_active_worktrees()
            if active_worktrees is None:
                active_worktrees = []

            html_content = self._generate_dashboard_html(improvements, stats, level_stats, active_worktrees, success_fail_stats)

            if not html_content or not html_content.strip():
                logger.error("Generated HTML content is empty, dashboard not updated")
                return

            dashboard_path = self.workspace_path / 'dashboard.html'
            dashboard_path.write_text(html_content)
            logger.info(f"Dashboard updated: {stats.get('completed', 0)} completed, {stats.get('pending', 0)} pending, {len(active_worktrees)} parallel")

        except Exception as e:
            logger.error(f"Failed to update dashboard: {e}. Check database connection and worktree manager.", exc_info=True)

    def _get_level_progress_indicator(self, imp: Optional[Dict], level: Optional[int]) -> str:
        """Generate visual progress indicator for current level (Plan → Execute → Test).

        Args:
            imp: Improvement dictionary with level-specific data
            level: Current level (1=MVP, 2=Enhanced, 3=Advanced)

        Returns:
            HTML string with progress indicator or fallback if invalid input
        """
        if imp is None or not isinstance(imp, dict):
            logger.warning("Invalid improvement dict in _get_level_progress_indicator")
            return '<span class="level-progress">○ → ○ → ○</span>'

        if level not in [1, 2, 3]:
            logger.warning(f"Invalid level {level} in _get_level_progress_indicator, defaulting to 1")
            level = 1

        level_prefix = {1: 'mvp', 2: 'enhanced', 3: 'advanced'}.get(level, 'mvp')

        has_plan = imp.get(f'{level_prefix}_plan') is not None
        has_output = imp.get(f'{level_prefix}_output') is not None
        test_status = imp.get(f'{level_prefix}_test_status', 'pending')

        if test_status not in ['pending', 'passed', 'failed']:
            logger.warning(f"Unknown test_status '{test_status}', defaulting to pending")
            test_status = 'pending'

        plan_icon = '●' if has_plan else '○'
        exec_icon = '●' if has_output else '○'

        if test_status == 'passed':
            test_icon = '✓'
        elif test_status == 'failed':
            test_icon = '✗'
        else:
            test_icon = '○'

        return f'<span class="level-progress">{plan_icon} → {exec_icon} → {test_icon}</span>'

    def _generate_dashboard_html(self, improvements: Optional[List[Dict]], stats: Optional[Dict],
                                   level_stats: Optional[Dict], active_worktrees: Optional[List[Path]] = None,
                                   success_fail_stats: Optional[Dict] = None) -> str:
        """Generate dashboard HTML with parallel task tracking.

        Args:
            improvements: List of improvement dicts from database
            stats: Overall statistics dict
            level_stats: Statistics by level dict
            active_worktrees: List of active worktree paths
            success_fail_stats: Success/fail test statistics

        Returns:
            Complete HTML string for dashboard
        """
        if improvements is None or not isinstance(improvements, list):
            logger.warning("Invalid improvements list, using empty list")
            improvements = []

        if stats is None or not isinstance(stats, dict):
            logger.warning("Invalid stats dict, using defaults")
            stats = {'pending': 0, 'testing': 0, 'in_progress': 0, 'completed': 0, 'total': 0}

        if level_stats is None:
            level_stats = {}

        if success_fail_stats is None or not isinstance(success_fail_stats, dict):
            success_fail_stats = {
                'total_passed': 0,
                'total_failed': 0,
                'success_rate': 0.0,
                'total_retries': 0,
                'avg_retries': 0.0
            }

        if active_worktrees is None or not isinstance(active_worktrees, list):
            active_worktrees = []

        active_ids = set()
        for wt in active_worktrees:
            try:
                if wt is None or not hasattr(wt, 'name'):
                    continue
                wt_id = int(wt.name.replace('wt-', ''))
                active_ids.add(wt_id)
            except (ValueError, AttributeError, TypeError) as e:
                logger.debug(f"Could not extract ID from worktree {wt}: {e}")
                continue

        # Sort: testing first, then in_progress, then pending, then completed
        status_order = {'testing': 0, 'in_progress': 1, 'pending': 2, 'completed': 3}
        sorted_improvements = sorted(
            improvements,
            key=lambda x: (status_order.get(x.get('status', 'pending'), 2), -x.get('priority', 50))
        )

        rows = []
        for imp in sorted_improvements:
            try:
                if imp is None or not isinstance(imp, dict):
                    logger.warning(f"Skipping invalid improvement entry: {imp}")
                    continue

                imp_id = imp.get('id', '?')
                title = imp.get('title', 'Unknown Feature')
                status = imp.get('status', 'pending')
                level = imp.get('current_level', 1)
                priority = imp.get('priority', 50)

                if level not in [1, 2, 3]:
                    logger.warning(f"Invalid level {level} for improvement {imp_id}, defaulting to 1")
                    level = 1

                level_name = LEVEL_NAMES.get(level, 'MVP')

                mvp_test = imp.get('mvp_test_status', 'pending')
                enh_test = imp.get('enhanced_test_status', 'pending')
                adv_test = imp.get('advanced_test_status', 'pending')

                mvp_icon = '✓' if mvp_test == 'passed' else ('✗' if mvp_test == 'failed' else '○')
                enh_icon = '✓' if enh_test == 'passed' else ('✗' if enh_test == 'failed' else '–')
                adv_icon = '✓' if adv_test == 'passed' else ('✗' if adv_test == 'failed' else '–')
                progress = f"{mvp_icon} | {enh_icon} | {adv_icon}"

                completed_level = "–"
                if adv_test == 'passed':
                    completed_level = "Advanced"
                elif enh_test == 'passed':
                    completed_level = "Enhanced"
                elif mvp_test == 'passed':
                    completed_level = "MVP"

                level_progress = self._get_level_progress_indicator(imp, level)

                is_parallel = imp_id in active_ids
                parallel_indicator = '⚡' if is_parallel else ''

                time_estimate = "–"
                if imp.get('estimated_remaining') is not None:
                    time_estimate = self._format_duration(imp['estimated_remaining'])

                status_class = status.replace('_', '-')

                # Use spinning indicator for parallel tasks
                if is_parallel:
                    parallel_indicator = '<span class="spinning-indicator">⚡</span>'

                rows.append(f'''
            <tr class="{status_class}{' parallel' if is_parallel else ''}">
                <td>{imp_id}</td>
                <td>{parallel_indicator} {html.escape(title)}</td>
                <td><span class="level-badge level-{level}">{level_name}</span> {level_progress}</td>
                <td class="progress-cell">{progress}</td>
                <td>{completed_level}</td>
                <td><span class="status-badge {status_class}">{status}</span></td>
                <td>{time_estimate}</td>
                <td>{priority}</td>
            </tr>''')

            except Exception as e:
                logger.warning(f"Error generating row for improvement {imp.get('id', '?')}: {e}")
                continue

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
        .stat-card.success .value {{ color: #22c55e; }}
        .stat-card.failure .value {{ color: #ef4444; }}
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
        tr.parallel {{ background: rgba(168,85,247,0.1); }}

        /* Animated sliding bar at bottom of active rows */
        tr.in-progress, tr.testing {{
            position: relative;
            overflow: visible;
        }}
        tr.in-progress::after, tr.testing::after {{
            content: '';
            position: absolute;
            bottom: 0;
            left: 0;
            height: 2px;
            width: 30%;
            border-radius: 2px;
            animation: slideBar 2s ease-in-out infinite;
        }}
        tr.in-progress::after {{
            background: linear-gradient(90deg, transparent, #f97316, #fb923c, transparent);
        }}
        tr.testing::after {{
            background: linear-gradient(90deg, transparent, #3b82f6, #60a5fa, transparent);
        }}
        @keyframes slideBar {{
            0% {{ left: 0%; }}
            50% {{ left: 70%; }}
            100% {{ left: 0%; }}
        }}

        /* Pulsing left border for active rows */
        tr.in-progress td:first-child {{
            border-left: 3px solid #f97316;
            animation: borderPulse 1.5s ease-in-out infinite;
        }}
        tr.testing td:first-child {{
            border-left: 3px solid #3b82f6;
            animation: borderPulse 1.2s ease-in-out infinite;
        }}
        @keyframes borderPulse {{
            0%, 100% {{ border-left-color: inherit; }}
            50% {{ border-left-color: rgba(255,255,255,0.3); }}
        }}

        /* Spinning indicator for parallel tasks */
        .spinning-indicator {{
            display: inline-block;
            animation: spin 1s linear infinite;
        }}
        @keyframes spin {{ 100% {{ transform: rotate(360deg); }} }}
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
            <div class="stat-card success">
                <div class="value">{success_fail_stats.get('total_passed', 0)}</div>
                <div class="label">Tests Passed</div>
            </div>
            <div class="stat-card failure">
                <div class="value">{success_fail_stats.get('total_failed', 0)}</div>
                <div class="label">Tests Failed</div>
            </div>
            <div class="stat-card success">
                <div class="value">{success_fail_stats.get('success_rate', 0.0)}%</div>
                <div class="label">Success Rate</div>
            </div>
            <div class="stat-card testing">
                <div class="value">{success_fail_stats.get('total_retries', 0)}</div>
                <div class="label">Total Retries</div>
            </div>
            <div class="stat-card testing">
                <div class="value">{success_fail_stats.get('avg_retries', 0.0)}</div>
                <div class="label">Avg Retries/Feature</div>
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
