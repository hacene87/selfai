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
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from .database import Database, MAX_PARALLEL_TASKS, MAX_TEST_ATTEMPTS

logger = logging.getLogger('selfai')

# Claude CLI command
CLAUDE_CMD = os.environ.get('CLAUDE_CMD', 'claude')


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

        # Setup logging
        self._setup_logging()

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
        """Acquire exclusive lock."""
        try:
            self.lock_fd = open(self.lock_file, 'w')
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.lock_fd.write(str(os.getpid()))
            self.lock_fd.flush()
            return True
        except (IOError, OSError):
            if self.lock_fd:
                self.lock_fd.close()
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

    def run(self):
        """Main run loop."""
        if not self.acquire_lock():
            logger.info("Another instance is running, skipping")
            return

        try:
            start_time = time.time()
            logger.info("=" * 50)
            logger.info("SelfAI Run Started")
            logger.info("=" * 50)

            stats = self.db.get_stats()
            logger.info(f"Stats: {stats}")

            tasks_processed = 0

            # Phase 1: Generate plans for pending tasks
            pending = self.db.get_pending_planning(limit=MAX_PARALLEL_TASKS)
            if pending:
                logger.info(f"Phase 1: Planning {len(pending)} tasks...")
                for task in pending:
                    if self.db.can_start_new_task():
                        self._generate_plan(task)
                        tasks_processed += 1

            # Phase 2: Execute approved tasks
            approved = self.db.get_approved_tasks(limit=MAX_PARALLEL_TASKS)
            if approved:
                logger.info(f"Phase 2: Executing {len(approved)} approved tasks...")
                self._execute_parallel(approved)
                tasks_processed += len(approved)

            # Phase 3: Test tasks that need testing
            testing = self.db.get_tasks_for_testing(limit=MAX_PARALLEL_TASKS)
            if testing:
                logger.info(f"Phase 3: Testing {len(testing)} tasks...")
                for task in testing:
                    self._run_test(task)
                    tasks_processed += 1

            # Phase 4: Resume in-progress tasks
            in_progress = self.db.get_in_progress(limit=MAX_PARALLEL_TASKS)
            if in_progress:
                logger.info(f"Phase 4: Resuming {len(in_progress)} in-progress tasks...")
                self._execute_parallel(in_progress)

            # Update dashboard
            self.update_dashboard()

            duration = time.time() - start_time
            logger.info(f"Run completed: {tasks_processed} tasks in {duration:.1f}s")

        except Exception as e:
            logger.error(f"Run failed: {e}")
        finally:
            self.release_lock()

    def _generate_plan(self, task: Dict):
        """Generate a detailed plan for a task using Claude with internet research."""
        imp_id = task['id']
        title = task['title']
        description = task.get('description', '')
        user_feedback = task.get('user_feedback', '')

        logger.info(f"Generating plan for #{imp_id}: {title}")
        self.db.mark_planning(imp_id)

        # Build planning prompt with internet research
        feedback_section = ""
        if user_feedback:
            feedback_section = f"""
## User Feedback (incorporate this)
{user_feedback}
"""

        prompt = f"""You are planning a feature implementation for the SelfAI project.

## Task
**Title:** {title}
**Description:** {description}
{feedback_section}
## Instructions
Create a DETAILED implementation plan. Research best practices from the internet.

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
                timeout=300,
                cwd=str(self.repo_path)
            )

            if result.returncode == 0 and result.stdout.strip():
                plan_content = result.stdout.strip()
                self.db.save_plan(imp_id, plan_content)
                logger.info(f"Plan generated for #{imp_id}, awaiting review")
            else:
                error = result.stderr or "No output from Claude"
                logger.error(f"Plan generation failed for #{imp_id}: {error}")
                self.db.mark_failed(imp_id, f"Plan generation failed: {error[:200]}")

        except subprocess.TimeoutExpired:
            logger.error(f"Plan generation timed out for #{imp_id}")
            self.db.mark_failed(imp_id, "Plan generation timed out")
        except Exception as e:
            logger.error(f"Plan generation error for #{imp_id}: {e}")
            self.db.mark_failed(imp_id, str(e))

    def _execute_parallel(self, tasks: List[Dict]):
        """Execute tasks in parallel (max 5)."""
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL_TASKS) as executor:
            futures = {executor.submit(self._execute_task, task): task for task in tasks}
            for future in as_completed(futures):
                task = futures[future]
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"Task #{task['id']} execution error: {e}")

    def _execute_task(self, task: Dict):
        """Execute a single approved task."""
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

    def _run_test(self, task: Dict):
        """Run tests for a task."""
        imp_id = task['id']
        title = task['title']
        test_count = task.get('test_count', 0)

        logger.info(f"Testing #{imp_id}: {title} (attempt {test_count + 1}/{MAX_TEST_ATTEMPTS})")

        prompt = f"""Test the implementation for: {title}

Run appropriate tests to verify the feature works correctly:
1. Check for syntax errors
2. Run unit tests if they exist
3. Test the feature manually if needed
4. Verify no regressions

If tests PASS, respond with: TEST_PASSED
If tests FAIL, respond with: TEST_FAILED followed by the error details
"""

        try:
            result = subprocess.run(
                [CLAUDE_CMD, '-p', prompt, '--allowedTools', 'Read,Bash,Glob,Grep'],
                capture_output=True,
                text=True,
                timeout=300,
                cwd=str(self.repo_path)
            )

            output = result.stdout.strip()

            if 'TEST_PASSED' in output:
                self.db.mark_test_passed(imp_id, output)
                logger.info(f"Tests passed for #{imp_id}")
                # Merge to main and push
                self._merge_and_push(imp_id, title)
            else:
                self.db.mark_test_failed(imp_id, output)
                logger.warning(f"Tests failed for #{imp_id}")

        except subprocess.TimeoutExpired:
            self.db.mark_test_failed(imp_id, "Test timed out")
        except Exception as e:
            self.db.mark_test_failed(imp_id, str(e))

    def _merge_and_push(self, imp_id: int, title: str):
        """Merge changes and push to origin."""
        try:
            # Add, commit, push
            subprocess.run(['git', 'add', '-A'], cwd=str(self.repo_path), check=True)

            commit_msg = f"[SelfAI] {title} (#{imp_id})\n\nAutomatically implemented by SelfAI"
            subprocess.run(
                ['git', 'commit', '-m', commit_msg],
                cwd=str(self.repo_path),
                capture_output=True
            )

            result = subprocess.run(
                ['git', 'push', 'origin', 'main'],
                cwd=str(self.repo_path),
                capture_output=True,
                text=True
            )

            if result.returncode == 0:
                logger.info(f"Pushed #{imp_id} to origin/main")
            else:
                logger.warning(f"Push failed for #{imp_id}: {result.stderr}")

        except Exception as e:
            logger.error(f"Merge/push error for #{imp_id}: {e}")

    def update_dashboard(self):
        """Update the HTML dashboard."""
        stats = self.db.get_stats()
        tasks = self.db.get_all()

        # Generate HTML
        html = self._generate_dashboard_html(stats, tasks)

        # Write dashboard
        dashboard_path = self.data_dir / 'dashboard.html'
        dashboard_path.write_text(html)
        logger.info(f"Dashboard updated: {stats}")

    def _generate_dashboard_html(self, stats: Dict, tasks: List[Dict]) -> str:
        """Generate dashboard HTML."""
        # Status colors
        status_colors = {
            'pending': '#6b7280',
            'planning': '#8b5cf6',
            'plan_review': '#f59e0b',
            'approved': '#10b981',
            'in_progress': '#3b82f6',
            'testing': '#6366f1',
            'completed': '#22c55e',
            'failed': '#ef4444',
            'cancelled': '#dc2626',
        }

        # Generate task rows
        rows = []
        for task in tasks:
            status = task.get('status', 'pending')
            color = status_colors.get(status, '#6b7280')

            # Plan preview (first 100 chars)
            plan = task.get('plan_content', '') or ''
            plan_preview = plan[:150].replace('"', '&quot;').replace('<', '&lt;').replace('\n', ' ')

            # Action buttons based on status
            actions = ''
            if status == 'plan_review':
                actions = f'''
                    <button onclick="approvePlan({task['id']})" class="btn-approve">Approve</button>
                    <button onclick="showFeedback({task['id']})" class="btn-feedback">Feedback</button>
                '''
            elif status == 'cancelled':
                actions = f'''
                    <button onclick="reEnable({task['id']})" class="btn-reenable">Re-enable</button>
                '''

            test_info = f"{task.get('test_count', 0)}/{MAX_TEST_ATTEMPTS}" if status in ['failed', 'cancelled', 'testing'] else '-'

            rows.append(f'''
            <tr class="{status}">
                <td>{task['id']}</td>
                <td>{task['title']}</td>
                <td><span class="status-badge" style="background: {color}20; color: {color}">{status}</span></td>
                <td class="plan-cell" title="{plan_preview}">{plan_preview[:50]}{'...' if len(plan_preview) > 50 else ''}</td>
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
        .btn-approve, .btn-feedback, .btn-reenable {{
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
        </div>

        <table>
            <thead>
                <tr>
                    <th>ID</th>
                    <th>Feature</th>
                    <th>Status</th>
                    <th>Plan Preview</th>
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

    <script>
        let currentTaskId = null;

        function approvePlan(id) {{
            if (confirm('Approve this plan for execution?')) {{
                fetch(`/api/approve/${{id}}`, {{ method: 'POST' }})
                    .then(() => location.reload())
                    .catch(() => alert('Use CLI: python -m selfai approve ' + id));
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
            fetch(`/api/feedback/${{currentTaskId}}`, {{
                method: 'POST',
                body: JSON.stringify({{ feedback }})
            }}).then(() => location.reload())
              .catch(() => alert('Use CLI: python -m selfai feedback ' + currentTaskId + ' "' + feedback + '"'));
            closeModal();
        }}

        function reEnable(id) {{
            if (confirm('Re-enable this cancelled task?')) {{
                fetch(`/api/reenable/${{id}}`, {{ method: 'POST' }})
                    .then(() => location.reload())
                    .catch(() => alert('Use CLI: python -m selfai reenable ' + id));
            }}
        }}
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
