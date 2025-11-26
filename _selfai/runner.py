"""Autonomous improvement runner with 3-level progressive complexity.

Each feature progresses through:
1. MVP: Simple, working implementation
2. Enhanced: Robust with edge cases, better error handling
3. Advanced: Production-ready, optimized, comprehensive

At each level: Plan → Execute → Test → (Pass: Next Level, Fail: Retry)

PARALLEL PROCESSING:
- Multiple tasks can be processed concurrently
- Batch testing for faster validation
- Continuous processing until timeout
"""
import os
import subprocess
import logging
import json
import html
import time
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional, List
from concurrent.futures import ThreadPoolExecutor, as_completed
from .database import Database, LEVEL_NAMES

logger = logging.getLogger('selfai')


class Runner:
    """Autonomous self-improving runner with 3-level feature progression.

    Features:
    - Parallel task processing with configurable workers
    - Batch testing for faster validation
    - Continuous processing mode
    - Smart model selection (haiku for simple, sonnet for complex)
    """

    CLAUDE_CMD = 'claude'
    LOCK_FILE = 'selfai.lock'

    # Parallel processing config
    MAX_WORKERS = 3  # Max concurrent tasks
    MAX_TASKS_PER_RUN = 5  # Max tasks to process in one run
    RUN_TIMEOUT = 600  # Max seconds per run cycle (10 min)

    # Level-specific guidance for plans
    LEVEL_GUIDANCE = {
        1: """MVP LEVEL - Keep it simple:
- Minimal working implementation
- Core functionality only
- Basic error handling
- No edge cases yet
- Quick implementation (5-10 min)""",

        2: """ENHANCED LEVEL - Make it robust:
- Build on MVP implementation
- Handle edge cases
- Better error messages
- Input validation
- More comprehensive tests
- Medium complexity (10-20 min)""",

        3: """ADVANCED LEVEL - Production ready:
- Optimize performance
- Complete error handling
- Security considerations
- Comprehensive documentation
- Full test coverage
- Production quality (20-30 min)"""
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
        self._setup_logging()

    def _setup_logging(self):
        """Setup file logging."""
        handler = logging.FileHandler(self.logs_path / 'runner.log')
        handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s'))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

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
        """Run improvement cycle with continuous processing.

        Processes multiple tasks in one run for efficiency:
        1. If no features exist, analyze existing codebase first
        2. Batch test all features waiting for testing (parallel)
        3. Process multiple pending improvements (parallel where safe)
        4. Only discover NEW features after all existing are complete

        Continues until MAX_TASKS_PER_RUN reached or RUN_TIMEOUT exceeded.
        """
        if not self.acquire_lock():
            return

        start_time = time.time()
        tasks_processed = 0

        try:
            stats = self.db.get_stats()

            # Phase 0: If no features exist, analyze existing codebase first
            if stats.get('total', 0) == 0:
                logger.info("No features in database - analyzing existing codebase...")
                self._discover_existing_features()
                return

            # Phase 1: Resume any stuck in_progress tasks first
            improvement = self.db.get_next_in_progress()
            if improvement:
                logger.info(f"Resuming: {improvement['title']} (Level {improvement['current_level']})")
                self._process_improvement(improvement)
                tasks_processed += 1

            # Phase 2: Batch test all features waiting for testing (PARALLEL)
            testing_batch = self._get_all_needs_testing()
            if testing_batch:
                logger.info(f"Batch testing {len(testing_batch)} features in parallel...")
                self._run_batch_tests(testing_batch)
                tasks_processed += len(testing_batch)

            # Phase 3: Process pending improvements (continuous until limit)
            while tasks_processed < self.MAX_TASKS_PER_RUN:
                # Check timeout
                if time.time() - start_time > self.RUN_TIMEOUT:
                    logger.info(f"Run timeout reached after {tasks_processed} tasks")
                    break

                improvement = self.db.get_next_pending()
                if not improvement:
                    break

                self._process_improvement(improvement)
                tasks_processed += 1

                # Check if feature needs testing immediately
                self.db.get_next_needs_testing()

            # Phase 4: Only discover NEW features after all existing are complete
            stats = self.db.get_stats()
            if stats.get('completed', 0) > 0 and stats.get('pending', 0) == 0 and stats.get('testing', 0) == 0:
                logger.info("All existing features tested - discovering new improvements...")
                self._run_discovery()

            logger.info(f"Run completed: {tasks_processed} tasks processed in {self._format_duration(time.time() - start_time)}")

        finally:
            self.release_lock()
            self.update_dashboard()
            self._check_self_deploy()

    def _get_all_needs_testing(self) -> List[Dict]:
        """Get all improvements that need testing."""
        results = []
        while True:
            imp = self.db.get_next_needs_testing()
            if not imp:
                break
            # Temporarily mark to avoid re-selecting
            results.append(imp)
            if len(results) >= self.MAX_WORKERS * 2:
                break
        return results

    def _run_batch_tests(self, improvements: List[Dict]):
        """Run tests for multiple improvements in parallel."""
        if not improvements:
            return

        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
            futures = {
                executor.submit(self._run_tests, imp): imp
                for imp in improvements
            }

            for future in as_completed(futures):
                imp = futures[future]
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"Test failed for {imp['title']}: {e}")

    def _run_tests(self, improvement: Dict):
        """Run tests for current level of improvement."""
        imp_id = improvement['id']
        title = improvement['title']
        level = improvement['current_level']
        level_name = LEVEL_NAMES[level]

        logger.info(f"Running {level_name} tests for: {title}")

        test_prompt = f'''You are testing a {level_name} implementation.

Repository: {self.repo_path}
Feature: {title}
Level: {level_name} ({level}/3)
Description: {improvement.get('description', '')}

TEST CRITERIA FOR {level_name}:
{self._get_test_criteria(level)}

INSTRUCTIONS:
1. Find and run any existing tests
2. Verify the {level_name} implementation works
3. Check for obvious bugs or issues
4. Report results

OUTPUT FORMAT:
```json
{{
  "test_passed": true/false,
  "tests_run": ["list of tests"],
  "issues_found": ["any issues"],
  "ready_for_next_level": true/false
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
        """Analyze codebase and catalog all EXISTING features that need testing."""
        logger.info("Analyzing existing codebase for implemented features...")

        prompt = f'''Analyze this repository and list ALL existing implemented features.

Repository: {self.repo_path}

YOUR TASK:
1. Read through all files in the repository
2. Identify every distinct feature, function, or capability that is ALREADY implemented
3. List each as a separate feature that needs to be tested

IMPORTANT:
- Only list features that ALREADY EXIST in the code
- Do NOT suggest new features to add
- Be thorough - find ALL existing functionality
- Each feature will go through MVP → Enhanced → Advanced testing

OUTPUT FORMAT:
```json
{{
  "existing_features": [
    {{
      "title": "Feature name (5-10 words)",
      "description": "What this feature does, which files implement it, key functions/classes",
      "category": "feature|testing|security|performance|utility",
      "priority": 1-100
    }}
  ]
}}
```

Example features to look for:
- Database operations
- CLI commands
- Core functionality
- Helper utilities
- Configuration handling
- Logging systems
- etc.'''

        result = self._execute_claude(prompt, timeout=600)
        if result['success']:
            self._parse_existing_features(result['output'])

    def _parse_existing_features(self, output: str):
        """Parse and add existing features to database."""
        try:
            json_str = self._extract_json(output)
            if json_str:
                data = json.loads(json_str)
                features = data.get('existing_features', [])
                added = 0
                for feat in features:
                    title = feat.get('title', '')
                    if title and not self.db.exists(title):
                        self.db.add(
                            title=title,
                            description=feat.get('description', ''),
                            category=feat.get('category', 'feature'),
                            priority=feat.get('priority', 50),
                            source='existing'
                        )
                        logger.info(f"Found existing feature: {title}")
                        added += 1
                logger.info(f"Added {added} existing features to database")
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
        """Fallback parser using regex when JSON fails."""
        import re
        # Look for patterns like "title": "..." or **Feature:**
        title_patterns = [
            r'"title":\s*"([^"]+)"',
            r'\*\*([^*]+)\*\*',
            r'^\d+\.\s+(.+?)(?:\n|$)',
        ]
        found = set()
        for pattern in title_patterns:
            matches = re.findall(pattern, output, re.MULTILINE)
            for title in matches:
                title = title.strip()
                if len(title) > 5 and len(title) < 100 and title not in found:
                    if not self.db.exists(title):
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
        """Discover NEW improvements for the repository (after existing are tested)."""
        completed = self.db.get_completed_features()
        completed_context = "\n".join([f"  - {f}" for f in completed[-10:]]) if completed else "  None yet"

        prompt = f'''Analyze this repository and suggest 3-5 NEW improvements to add.

Repository: {self.repo_path}

ALREADY IMPLEMENTED (do not duplicate):
{completed_context}

IMPORTANT:
- Read existing files to understand the codebase
- Focus on gaps and missing functionality
- Each improvement will go through MVP → Enhanced → Advanced progression
- Be specific with file paths and requirements

OUTPUT FORMAT:
```json
{{
  "improvements": [
    {{
      "title": "Clear 5-10 word title",
      "description": "Detailed description: WHY needed, WHAT to change, HOW it fits",
      "category": "feature|testing|security|performance|refactoring",
      "priority": 1-100
    }}
  ]
}}
```'''

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

    def _process_improvement(self, improvement: Dict):
        """Process improvement at its current level."""
        imp_id = improvement['id']
        title = improvement['title']
        level = improvement['current_level']
        level_name = LEVEL_NAMES[level]
        start_time = time.time()

        logger.info(f"Processing {level_name}: {title}")
        self.db.mark_in_progress(imp_id)
        self.update_dashboard()

        # Get or create plan for this level
        plan = self.db.get_plan(imp_id, level)
        if not plan:
            plan = self._create_plan(improvement, level)
            if not plan:
                self.db.mark_failed(imp_id, "Planning failed")
                return
            self.db.save_plan(imp_id, level, plan)

        # Execute the plan
        output = self._execute_plan(improvement, level, plan)
        duration = time.time() - start_time

        if output:
            self.db.mark_level_completed(imp_id, level, output)
            logger.info(f"✓ {level_name} completed: {title} ({self._format_duration(duration)})")
        else:
            self.db.mark_failed(imp_id, "Execution failed")
            logger.error(f"✗ {level_name} failed: {title}")

    def _create_plan(self, improvement: Dict, level: int) -> Optional[str]:
        """Create execution plan for specific level."""
        title = improvement['title']
        level_name = LEVEL_NAMES[level]

        # Get previous level's output for context
        prev_context = ""
        if level > 1:
            prev_level_col = {2: 'mvp', 3: 'enhanced'}[level]
            prev_output = improvement.get(f'{prev_level_col}_output', '')
            if prev_output:
                prev_context = f"\nPREVIOUS LEVEL OUTPUT:\n{prev_output[:2000]}"

        prompt = f'''Create a {level_name} implementation plan.

Repository: {self.repo_path}
Feature: {title}
Description: {improvement.get('description', '')}
Level: {level_name} ({level}/3)
{prev_context}

{self.LEVEL_GUIDANCE[level]}

OUTPUT FORMAT:
## Analysis
[What exists, what needs to change]

## Plan
1. [Specific step]
2. [Specific step]
...

## Files to Modify
- [file]: [changes]

## Tests to Add
- [test description]'''

        result = self._execute_claude(prompt, timeout=180)
        if result['success']:
            logger.info(f"Plan created for {level_name}: {title}")
            return result.get('output', '')
        logger.error(f"Planning failed for: {title}")
        return None

    def _execute_plan(self, improvement: Dict, level: int, plan: str) -> Optional[str]:
        """Execute the plan for specific level."""
        title = improvement['title']
        level_name = LEVEL_NAMES[level]

        prompt = f'''Execute this {level_name} plan.

Repository: {self.repo_path}
Feature: {title}
Level: {level_name} ({level}/3)

PLAN:
{plan}

INSTRUCTIONS:
1. Follow the plan exactly
2. Make the code changes
3. Keep it at {level_name} complexity level
4. Do NOT create documentation unless specified

Execute now.'''

        result = self._execute_claude(prompt, timeout=900)
        if result['success']:
            return result.get('output', '')
        return None

    def _execute_claude(self, prompt: str, timeout: int = 300) -> Dict:
        """Execute Claude CLI command."""
        try:
            result = subprocess.run(
                [self.CLAUDE_CMD, '-p', prompt, '--allowedTools',
                 'Edit', 'Write', 'Bash', 'Glob', 'Grep', 'Read'],
                capture_output=True, text=True, timeout=timeout, cwd=str(self.repo_path)
            )
            return {'success': result.returncode == 0, 'output': result.stdout, 'error': result.stderr}
        except subprocess.TimeoutExpired:
            logger.warning(f"Claude call timed out after {timeout}s")
            return {'success': False, 'error': f'Timeout after {timeout}s'}
        except FileNotFoundError:
            return {'success': False, 'error': 'Claude CLI not found'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def _format_duration(self, seconds: float) -> str:
        """Format duration in human readable form."""
        if seconds < 60:
            return f"{int(seconds)}s"
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}m {secs}s"

    def update_dashboard(self):
        """Update HTML dashboard."""
        stats = self.db.get_stats()
        level_stats = self.db.get_level_stats()
        improvements = self.db.get_all()

        html_content = self._generate_dashboard_html(improvements, stats, level_stats)
        dashboard_path = self.workspace_path / 'dashboard.html'
        dashboard_path.write_text(html_content)
        logger.info(f"Dashboard updated: {stats.get('completed', 0)} completed, {stats.get('pending', 0)} pending")

    def _generate_dashboard_html(self, improvements: list, stats: dict, level_stats: dict) -> str:
        """Generate dashboard HTML."""
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

            status_class = status.replace('_', '-')
            rows.append(f'''
            <tr class="{status_class}">
                <td>{imp['id']}</td>
                <td>{html.escape(imp['title'])}</td>
                <td><span class="level-badge level-{level}">{level_name}</span></td>
                <td class="progress-cell">{progress}</td>
                <td>{completed_level}</td>
                <td><span class="status-badge {status_class}">{status}</span></td>
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
        .progress-cell {{ font-family: monospace; letter-spacing: 2px; }}
        tr.completed {{ opacity: 0.7; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>SelfAI</h1>
        <p class="subtitle">3-Level Progressive Improvement System</p>

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
                    <th>Priority</th>
                </tr>
            </thead>
            <tbody>
                {''.join(rows) if rows else '<tr><td colspan="7" style="text-align:center;color:#888;">No improvements yet</td></tr>'}
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
