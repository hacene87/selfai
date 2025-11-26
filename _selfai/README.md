# SelfAI - Autonomous Improvement System

An autonomous system that continuously analyzes and improves the codebase using Claude AI.

## Overview

SelfAI is a background service that:
- Discovers potential improvements by analyzing the codebase
- Plans and executes improvements autonomously
- Tracks progress via an HTML dashboard
- Runs automatically via macOS LaunchAgent

## Features

- **Autonomous Discovery**: Analyzes codebase for potential improvements (security, tests, refactoring, etc.)
- **Intelligent Planning**: Creates detailed plans before implementation
- **Priority System**: Resumes stuck in-progress tasks before processing pending ones
- **Plan Reuse**: Stores plans to avoid regeneration on retry
- **Time Tracking**: Logs task duration in human-readable format
- **Lock Mechanism**: Prevents concurrent execution using PID-based locks
- **HTML Dashboard**: Live dashboard with auto-refresh every 3 minutes
- **LaunchAgent Integration**: Automatic execution every 5 minutes

## Architecture

```
_selfai/
├── __init__.py          # Package initialization
├── __main__.py          # CLI entry point and LaunchAgent installer
├── runner.py            # Main execution engine
├── database.py          # SQLite database for tracking improvements
├── data/
│   ├── improvements.db  # SQLite database
│   └── selfai.lock      # PID-based lock file
├── logs/
│   ├── runner.log       # Main execution log
│   ├── launchd.log      # LaunchAgent stdout
│   └── launchd_error.log # LaunchAgent stderr
└── dashboard.html       # Auto-generated progress dashboard
```

## Installation

### Install LaunchAgent (Automatic Startup)

```bash
python3 -m _selfai install
```

This creates a LaunchAgent that runs every 5 minutes.

### Uninstall LaunchAgent

```bash
python3 -m _selfai uninstall
```

## Usage

### Manual Execution

```bash
# Run once
python3 -m _selfai run

# Check status
python3 -m _selfai status

# View dashboard
open _selfai/dashboard.html
```

### Monitor Logs

```bash
# Watch runner logs
tail -f _selfai/logs/runner.log

# Check LaunchAgent status
launchctl list | grep selfai
```

## Dashboard

The HTML dashboard provides a visual overview of:
- Completed improvements (green)
- In-progress tasks (orange)
- Pending improvements (yellow)

**Features:**
- Auto-refresh every 3 minutes
- Filter by status (completed/in-progress/pending)
- View detailed plans for each improvement
- Shows task duration and timestamps

**Access:** Open `_selfai/dashboard.html` in your browser

## Database Schema

```sql
CREATE TABLE improvements (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    category TEXT,
    priority TEXT DEFAULT 'medium',
    status TEXT DEFAULT 'pending',
    plan TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP
);
```

## Task Lifecycle

1. **Discovery** (15 min timeout)
   - Runner analyzes codebase for improvements
   - Creates improvement entries with status='pending'

2. **Planning** (3 min timeout)
   - Generates detailed implementation plan
   - Stores plan in database for reuse on retry

3. **Execution** (60 min timeout)
   - Implements the improvement using Claude CLI
   - Updates status to 'completed' on success

4. **Priority Handling**
   - Stuck 'in_progress' tasks (from crashed/expired processes) are resumed first
   - Then 'pending' tasks are processed
   - Finally, discovery runs if no pending improvements exist

## Configuration

### Timeouts
- **Discovery**: 15 minutes (DISCOVERY_TIMEOUT)
- **Planning**: 3 minutes (PLANNING_TIMEOUT)
- **Execution**: 60 minutes (EXECUTION_TIMEOUT)
- **LaunchAgent interval**: 5 minutes (StartInterval)

### Lock Mechanism
- Uses PID-based lock file at `_selfai/data/selfai.lock`
- Prevents concurrent execution
- Automatically released on process exit

## Technical Details

### Plan Storage
Plans are JSON-encoded and HTML-escaped for safe storage in the dashboard:
```python
plan_json = json.dumps(plan_raw)
plan_escaped = html.escape(plan_json, quote=True)
```

JavaScript decodes using `JSON.parse()` for proper display.

### In-Progress Task Resume
On startup, checks for tasks with status='in_progress' (likely from crashed processes):
```python
improvement = self.db.get_next_in_progress()
if improvement:
    logger.info(f"Resuming stuck in_progress task: {improvement['title']}")
```

### Time Tracking
Logs human-readable duration on completion:
```python
duration = time.time() - start_time
minutes = int(duration // 60)
seconds = int(duration % 60)
logger.info(f"Completed: {title} (took {minutes}m {seconds}s)")
```

## Troubleshooting

### Check if LaunchAgent is running
```bash
launchctl list | grep selfai
ps aux | grep "_selfai run"
```

### View error logs
```bash
cat _selfai/logs/launchd_error.log
tail -50 _selfai/logs/runner.log
```

### Clear stuck lock
```bash
rm _selfai/data/selfai.lock
```

### Reset database
```bash
rm _selfai/data/improvements.db
python3 -m _selfai run  # Will recreate
```

## Development

### Run with debugging
```bash
cd "/Users/hacenemeziani/Documents/github/odoo framework"
python3 -m _selfai run
```

### Update dashboard manually
```python
from _selfai.runner import Runner
from pathlib import Path
Runner(Path.cwd()).update_dashboard()
```

### Query database
```bash
sqlite3 _selfai/data/improvements.db "SELECT * FROM improvements WHERE status='pending';"
```

## Notes

- The `_selfai` folder is gitignored to keep runtime data private
- Uses underscore prefix (`_selfai`) instead of dot (`.selfai`) for Python module compatibility
- All file paths are relative to repository root for LaunchAgent compatibility
- Dashboard auto-refresh uses HTML meta tag: `<meta http-equiv="refresh" content="180">`

## Credits

Generated with Claude Code (https://claude.com/claude-code)
