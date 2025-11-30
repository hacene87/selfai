"""Git Worktree Manager for isolated parallel task execution."""
import logging
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .exceptions import GitOperationError, ResourceLimitError, ValidationError, WorktreeConflictError

logger = logging.getLogger('selfai')


class WorktreeManager:
    """Manages git worktrees for isolated parallel task execution.

    Git worktrees enable multiple working directories from a single repository,
    each linked to a specific branch. This allows running multiple AI coding
    sessions in parallel while maintaining isolated context domains.
    """

    MIN_DISK_SPACE_MB = 500
    MAX_RETRIES = 3
    RETRY_DELAY = 2

    def __init__(self, repo_path: Path, worktrees_dir: Path):
        """Initialize WorktreeManager.

        Args:
            repo_path: Path to the main git repository
            worktrees_dir: Directory to store all worktrees
        """
        self.repo_path = repo_path
        self.worktrees_dir = worktrees_dir
        self.worktrees_dir.mkdir(parents=True, exist_ok=True)
        self._active_worktrees: Dict[int, Path] = {}  # task_id -> worktree_path

    def create_worktree(self, task_id: int, task_title: str) -> Optional[Path]:
        """Create isolated worktree for task execution.

        Args:
            task_id: Task ID
            task_title: Task title (used for branch name)

        Returns:
            Path to created worktree, or None if creation failed
        """
        # Check disk space before creating
        if not self._check_disk_space():
            return None

        # Check for existing worktree
        if task_id in self._active_worktrees:
            raise WorktreeConflictError(
                f"Worktree already exists for task #{task_id}",
                context={'task_id': task_id, 'path': self._active_worktrees[task_id]}
            )

        # Sanitize branch name
        branch_name = self._sanitize_branch_name(task_title)
        branch_name = f"selfai/task-{task_id}-{branch_name}"

        # Create worktree path
        worktree_path = self.worktrees_dir / f"task-{task_id}"

        try:
            logger.info(f"Creating worktree for #{task_id} at {worktree_path}")

            # Create branch and worktree
            success, message = self._run_git(
                'worktree', 'add', '-b', branch_name, str(worktree_path), 'main'
            )

            if not success:
                logger.error(f"Failed to create worktree for #{task_id}: {message}")
                return None

            # Track active worktree
            self._active_worktrees[task_id] = worktree_path

            logger.info(f"Worktree created for #{task_id}: {branch_name}")
            return worktree_path

        except Exception as e:
            logger.error(f"Error creating worktree for #{task_id}: {e}")
            # Cleanup on failure
            self.cleanup_worktree(task_id, force=True)
            return None

    def cleanup_worktree(self, task_id: int, force: bool = False) -> bool:
        """Remove worktree and associated branch.

        Args:
            task_id: Task ID
            force: Force cleanup even if there are uncommitted changes

        Returns:
            True if cleanup succeeded, False otherwise
        """
        if task_id not in self._active_worktrees:
            logger.warning(f"No active worktree found for #{task_id}")
            return False

        worktree_path = self._active_worktrees[task_id]

        try:
            logger.info(f"Cleaning up worktree for #{task_id}")

            # Get branch name before removing worktree
            success, branch_name = self._run_git(
                'rev-parse', '--abbrev-ref', 'HEAD',
                cwd=worktree_path,
                retry=False
            )

            # Remove worktree
            force_flag = '--force' if force else ''
            args = ['worktree', 'remove', str(worktree_path)]
            if force:
                args.insert(2, '--force')

            success, message = self._run_git(*args)

            if not success:
                logger.error(f"Failed to remove worktree for #{task_id}: {message}")
                return False

            # Delete branch if it exists
            if success and branch_name:
                self._run_git('branch', '-D', branch_name, retry=False)

            # Remove from tracking
            del self._active_worktrees[task_id]

            logger.info(f"Worktree cleanup complete for #{task_id}")
            return True

        except Exception as e:
            logger.error(f"Error cleaning up worktree for #{task_id}: {e}")
            return False

    def merge_to_main(self, task_id: int, task_title: str) -> Tuple[bool, str]:
        """Merge feature branch to main with conflict detection.

        Args:
            task_id: Task ID
            task_title: Task title for commit message

        Returns:
            Tuple of (success, message)
        """
        if task_id not in self._active_worktrees:
            return False, f"No active worktree for task #{task_id}"

        worktree_path = self._active_worktrees[task_id]

        try:
            # Get branch name
            success, branch_name = self._run_git(
                'rev-parse', '--abbrev-ref', 'HEAD',
                cwd=worktree_path,
                retry=False
            )

            if not success:
                return False, f"Failed to get branch name: {branch_name}"

            logger.info(f"Merging {branch_name} to main for #{task_id}")

            # Switch to main branch in main repo
            success, message = self._run_git('checkout', 'main')
            if not success:
                return False, f"Failed to checkout main: {message}"

            # Pull latest changes
            success, message = self._run_git('pull', 'origin', 'main', retry=True)
            if not success:
                logger.warning(f"Pull failed (may be OK if no remote): {message}")

            # Attempt merge
            success, message = self._run_git('merge', '--no-ff', branch_name, '-m',
                                            f"[SelfAI] {task_title} (#{task_id})")

            if not success:
                # Check for conflicts
                has_conflicts, conflicted_files = self._detect_merge_conflicts()
                if has_conflicts:
                    return False, f"Merge conflicts detected: {', '.join(conflicted_files)}"
                return False, f"Merge failed: {message}"

            logger.info(f"Successfully merged #{task_id} to main")
            return True, "Merge successful"

        except Exception as e:
            logger.error(f"Merge error for #{task_id}: {e}")
            return False, str(e)

    def _detect_merge_conflicts(self) -> Tuple[bool, List[str]]:
        """Check for merge conflicts without completing merge.

        Returns:
            Tuple of (has_conflicts, list of conflicted files)
        """
        result = subprocess.run(
            ['git', 'diff', '--name-only', '--diff-filter=U'],
            capture_output=True,
            text=True,
            cwd=self.repo_path,
            timeout=30
        )

        conflicted_files = result.stdout.strip().split('\n') if result.stdout.strip() else []
        return len(conflicted_files) > 0, conflicted_files

    def resolve_conflicts_with_claude(
        self,
        task_id: int,
        task_title: str,
        conflicted_files: List[str]
    ) -> bool:
        """Use Claude to intelligently resolve merge conflicts.

        Args:
            task_id: Task ID
            task_title: Task title
            conflicted_files: List of files with conflicts

        Returns:
            True if conflicts resolved successfully, False otherwise
        """
        logger.info(f"Attempting to resolve conflicts for #{task_id} with Claude")

        try:
            # Build context for Claude
            conflict_context = []
            for file_path in conflicted_files:
                full_path = self.repo_path / file_path
                if full_path.exists():
                    content = full_path.read_text()
                    conflict_context.append(f"File: {file_path}\n{content}\n")

            context_str = "\n".join(conflict_context)

            # Build prompt for Claude
            prompt = f"""Resolve merge conflicts for task: {task_title}

The following files have merge conflicts:
{', '.join(conflicted_files)}

Conflict markers (<<<<<<, ======, >>>>>>) indicate conflicting changes.

## Conflicted Files
{context_str}

## Instructions
1. Carefully review each conflict
2. Choose the correct resolution that preserves both sets of changes where possible
3. Remove conflict markers
4. Ensure code is syntactically correct
5. Use Edit tool to fix each file
6. Respond with: CONFLICTS_RESOLVED when done

Focus on maintaining functionality from both branches.
"""

            # Call Claude to resolve conflicts
            import os
            CLAUDE_CMD = os.environ.get('CLAUDE_CMD', 'claude')

            result = subprocess.run(
                [CLAUDE_CMD, '-p', prompt, '--allowedTools', 'Read,Edit,Bash'],
                capture_output=True,
                text=True,
                timeout=300,  # 5 minutes for conflict resolution
                cwd=str(self.repo_path)
            )

            output = result.stdout.strip()

            if 'CONFLICTS_RESOLVED' in output and result.returncode == 0:
                # Verify conflicts are actually resolved
                has_conflicts, remaining = self._detect_merge_conflicts()
                if not has_conflicts:
                    # Add resolved files
                    for file_path in conflicted_files:
                        self._run_git('add', file_path, retry=False)

                    # Commit resolution
                    self._run_git(
                        'commit', '-m',
                        f"[SelfAI] Resolve conflicts for #{task_id}: {task_title}"
                    )

                    logger.info(f"Conflicts resolved for #{task_id}")
                    return True
                else:
                    logger.error(f"Claude resolved conflicts but some remain: {remaining}")
                    return False
            else:
                logger.error(f"Claude failed to resolve conflicts: {output[:200]}")
                return False

        except subprocess.TimeoutExpired:
            logger.error(f"Conflict resolution timed out for #{task_id}")
            return False
        except Exception as e:
            logger.error(f"Error resolving conflicts for #{task_id}: {e}")
            return False

    def _sanitize_branch_name(self, title: str) -> str:
        """Convert title to valid git branch name.

        Args:
            title: Task title

        Returns:
            Sanitized branch name
        """
        # Convert to lowercase
        name = title.lower()

        # Replace spaces and special chars with hyphens
        name = re.sub(r'[^a-z0-9]+', '-', name)

        # Remove leading/trailing hyphens
        name = name.strip('-')

        # Limit length
        name = name[:50]

        return name or 'task'

    def _check_disk_space(self) -> bool:
        """Verify sufficient disk space before creating worktree.

        Returns:
            True if sufficient space available

        Raises:
            ResourceLimitError: If insufficient disk space
        """
        try:
            stat = shutil.disk_usage(self.repo_path)
            free_mb = stat.free / (1024 * 1024)

            if free_mb < self.MIN_DISK_SPACE_MB:
                raise ResourceLimitError(
                    f"Insufficient disk space: {free_mb:.0f}MB available, "
                    f"{self.MIN_DISK_SPACE_MB}MB required"
                )

            return True

        except Exception as e:
            logger.error(f"Error checking disk space: {e}")
            return False

    def _run_git(self, *args, cwd: Path = None, retry: bool = True) -> Tuple[bool, str]:
        """Execute git command with retry logic and proper error handling.

        Args:
            *args: Git command arguments
            cwd: Working directory (defaults to repo_path)
            retry: Enable retry logic for transient failures

        Returns:
            Tuple of (success, output/error message)
        """
        work_dir = cwd or self.repo_path

        for attempt in range(self.MAX_RETRIES if retry else 1):
            try:
                result = subprocess.run(
                    ['git'] + list(args),
                    cwd=str(work_dir),
                    capture_output=True,
                    text=True,
                    timeout=30
                )

                if result.returncode == 0:
                    return True, result.stdout.strip()

                # Check if error is retryable (lock files)
                stderr = result.stderr.strip()
                if not retry or 'lock' not in stderr.lower():
                    return False, stderr

                # Exponential backoff
                if attempt < self.MAX_RETRIES - 1:
                    wait_time = self.RETRY_DELAY * (2 ** attempt)
                    logger.warning(
                        f"Git operation failed (attempt {attempt + 1}/{self.MAX_RETRIES}), "
                        f"retrying in {wait_time}s..."
                    )
                    time.sleep(wait_time)

            except subprocess.TimeoutExpired:
                if attempt < self.MAX_RETRIES - 1 and retry:
                    time.sleep(self.RETRY_DELAY)
                else:
                    return False, "Git operation timed out"
            except Exception as e:
                return False, str(e)

        return False, "Max retries exceeded"

    def validate_task(self, task: Dict) -> None:
        """Validate task before worktree creation.

        Args:
            task: Task dictionary

        Raises:
            ValidationError: If task is invalid
            WorktreeConflictError: If worktree already exists
        """
        if not task.get('id'):
            raise ValidationError("Task ID required")

        if not task.get('title'):
            raise ValidationError("Task title required")

        if not task.get('plan_content'):
            raise ValidationError("Task must have approved plan")

        # Check for existing worktree
        if task['id'] in self._active_worktrees:
            raise WorktreeConflictError(
                f"Worktree already exists for task #{task['id']}",
                context={'task_id': task['id']}
            )

    def get_active_worktrees(self) -> Dict[int, Path]:
        """Get dictionary of active worktrees.

        Returns:
            Dictionary mapping task_id to worktree path
        """
        return self._active_worktrees.copy()

    def prune_orphaned_worktrees(self) -> int:
        """Clean up orphaned worktrees from previous runs.

        Returns:
            Number of worktrees cleaned up
        """
        logger.debug("Pruning orphaned worktrees...")

        try:
            # Use git worktree prune
            success, message = self._run_git('worktree', 'prune', retry=False)

            if not success:
                logger.warning(f"Worktree prune failed: {message}")
                return 0

            # Clean up orphaned directories
            cleanup_count = 0
            if self.worktrees_dir.exists():
                for worktree_dir in self.worktrees_dir.iterdir():
                    if worktree_dir.is_dir():
                        # Check if it's a valid git worktree
                        git_dir = worktree_dir / '.git'
                        if not git_dir.exists():
                            logger.info(f"Removing orphaned directory: {worktree_dir}")
                            shutil.rmtree(worktree_dir)
                            cleanup_count += 1

            if cleanup_count > 0:
                logger.info(f"Cleaned up {cleanup_count} orphaned worktrees")

            return cleanup_count

        except Exception as e:
            logger.error(f"Error pruning worktrees: {e}")
            return 0
