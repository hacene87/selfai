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
from .test_environment import TestEnvironmentManager
from .discovery import DiscoveryEngine, DiscoveryCategory
from .monitoring import SelfHealingMonitor

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

        # Initialize test environment manager for isolated testing
        self.test_env_manager = TestEnvironmentManager(
            repo_path,
            max_environments=MAX_PARALLEL_TASKS
        )

        # Setup logging
        self._setup_logging()

        # Initialize self-healing monitor
        self.monitor = SelfHealingMonitor(repo_path)

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
        """Main run loop.

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
        """Generate a detailed plan for a task, reusing existing plan if available."""
        imp_id = task['id']
        title = task['title']
        description = task.get('description', '')
        user_feedback = task.get('user_feedback', '')

        # Check for plan reuse (for retried tasks)
        existing_plan = self.db.get_plan_for_reuse(imp_id)
        if existing_plan and not user_feedback:
            logger.info(f"Reusing existing plan for #{imp_id}: {title}")
            self.db.save_plan(imp_id, existing_plan)
            return

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
        """Run tests for a task in isolated environment."""
        imp_id = task['id']
        title = task['title']
        test_count = task.get('test_count', 0)

        logger.info(f"Testing #{imp_id}: {title} (attempt {test_count + 1}/{MAX_TEST_ATTEMPTS})")

        # Create isolated test environment
        test_env = None
        try:
            test_env = self.test_env_manager.create_environment(imp_id)

            prompt = f"""Test the implementation for: {title}

Run appropriate tests to verify the feature works correctly:
1. Check for syntax errors
2. Run unit tests if they exist
3. Test the feature manually if needed
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
            logger.error(f"Test execution error for #{imp_id}: {e}")
            self.db.mark_test_failed(imp_id, str(e))
        finally:
            # Always cleanup the test environment
            if test_env:
                self.test_env_manager.release_environment(imp_id)

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
        discovery_stats = self.db.get_discovery_stats()

        # Generate HTML
        html = self._generate_dashboard_html(stats, tasks, discovery_stats)

        # Write dashboard
        dashboard_path = self.data_dir / 'dashboard.html'
        dashboard_path.write_text(html)
        logger.info(f"Dashboard updated: {stats}")

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

            # Action buttons based on status
            actions = ''
            if plan:
                actions += f'''<button onclick="showPlan({task['id']})" class="btn-view">View Plan</button>'''
            if status == 'plan_review':
                actions += f'''
                    <button onclick="approvePlan({task['id']})" class="btn-approve">Approve</button>
                    <button onclick="showFeedback({task['id']})" class="btn-feedback">Feedback</button>
                '''
            elif status == 'cancelled':
                actions += f'''
                    <button onclick="reEnable({task['id']})" class="btn-reenable">Re-enable</button>
                '''

            test_info = f"{task.get('test_count', 0)}/{MAX_TEST_ATTEMPTS}" if status in ['failed', 'cancelled', 'testing'] else '-'

            rows.append(f'''
            <tr class="{status}">
                <td>{task['id']}</td>
                <td>{task['title']}</td>
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
