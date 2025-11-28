# Self-Diagnosis and Log Analysis

## Overview
The Self-Diagnosis and Log Analysis feature automatically analyzes system logs, detects errors, and diagnoses problems. It uses pattern recognition to identify recurring issues, learns from past fixes, and can suggest proactive improvements to prevent future problems.

## Features

### 1. Pattern Detection
- Automatically identifies ERROR, EXCEPTION, TIMEOUT, FAILED, and CONFLICT patterns in logs
- Extracts contextual information including timestamps and full error messages
- Configurable pattern matching using regex for extensibility

### 2. Automated Diagnosis
- Uses Claude AI to analyze and diagnose detected issues
- Provides root cause analysis, fix recommendations, and prevention strategies
- Returns confidence scores for diagnostic accuracy

### 3. Self-Healing / Learning
- Stores successful fixes in a persistent pattern library
- Confidence scores improve with repeated successful fixes
- Automatically applies known fixes for recurring issues
- Tracks success rates and refines diagnostic accuracy over time

### 4. Proactive Improvements
- Analyzes system health trends based on log patterns
- Suggests improvements for reliability, performance, and code quality
- Automatically creates improvement tasks when patterns are detected

## Usage

### Automatic Analysis (During Runs)
Log analysis runs automatically in **Phase 5** of each SelfAI run:

```bash
python -m selfai run
```

The system will:
1. Analyze recent logs (last 10,000 lines by default)
2. Detect and save any issues found
3. Auto-diagnose up to 3 critical issues per run
4. Suggest proactive improvements if >5 tasks completed

### Manual Analysis
You can manually analyze logs and diagnose issues:

```bash
# Analyze logs for errors
python -m selfai analyze-logs

# Diagnose issues found in logs
python -m selfai diagnose
```

### Programmatic Usage
```python
from pathlib import Path
from selfai.runner import LogAnalyzer

# Initialize analyzer
data_dir = Path('.selfai_data')
analyzer = LogAnalyzer(data_dir, 'claude')

# Analyze logs
analysis = analyzer.analyze_logs()
print(f"Found {analysis['issues_found']} issues")

# Diagnose an issue
issue = analysis['issues'][0]
diagnosis = analyzer.diagnose_and_fix(issue, Path.cwd())
print(f"Diagnosis: {diagnosis['diagnosis']}")
print(f"Confidence: {diagnosis['confidence']}")
```

## Pattern Library

The pattern library is stored in `.selfai_data/patterns.json` and contains:

- **Issue Type**: Type of error (error, exception, timeout, etc.)
- **Pattern**: The error message pattern
- **Diagnosis**: Root cause analysis
- **Fix**: Recommended fix
- **Confidence**: Confidence score (0.0-1.0)
- **Success Count**: Number of times this fix has worked
- **Timestamps**: First seen and last seen dates

### Example Pattern Entry
```json
{
  "issue_type": "error",
  "pattern": "Database connection failed",
  "diagnosis": "Connection timeout due to network latency",
  "fix": "Increase connection timeout from 5s to 30s",
  "confidence": 0.92,
  "success_count": 5,
  "timestamp": "2025-01-28T10:00:00",
  "last_seen": "2025-01-28T15:30:00"
}
```

### Pattern Confidence
- **Initial**: 0.5 (from Claude diagnosis)
- **After each success**: confidence × 1.1 (capped at 0.99)
- **Threshold for auto-fix**: 0.7
- **Threshold for similar matching**: 0.8

## Configuration

### Adjustable Parameters in `runner.py`

```python
# In LogAnalyzer class
self.error_patterns = [
    (r'ERROR[:\s]+(.+)', 'error'),
    (r'Exception[:\s]+(.+)', 'exception'),
    (r'Failed[:\s]+(.+)', 'failure'),
    (r'Timeout[:\s]+(.+)', 'timeout'),
    (r'CONFLICT[:\s]+(.+)', 'conflict'),
]

# In SelfAIRunner.run() - Phase 5
analysis = self.log_analyzer.analyze_logs(max_lines=10000)  # Adjust max lines
critical_issues[:3]  # Max auto-fixes per run
stats.get('completed', 0) > 5  # Threshold for proactive improvements
```

### Adding Custom Patterns

Add new error patterns to detect:

```python
analyzer = LogAnalyzer(data_dir, 'claude')
analyzer.error_patterns.append(
    (r'CRITICAL[:\s]+(.+)', 'critical')
)
```

## Database Tracking

The database tracks diagnostic metrics for each improvement:

- `diagnosed_issues`: Number of diagnostic attempts
- `auto_fixed_issues`: Number of successful auto-fixes
- `diagnostic_confidence`: Latest confidence score

Access via:
```python
db.record_diagnosis(imp_id=5, confidence=0.85, fixed=True)
```

## API Reference

### LogAnalyzer

#### `__init__(data_dir: Path, claude_cmd: str)`
Initialize the log analyzer.

#### `analyze_logs(max_lines: int = 10000) -> Dict`
Analyze recent logs for errors and patterns.

**Returns:**
```python
{
    'log_lines': 1500,
    'issues': [
        {
            'type': 'error',
            'detail': 'Database connection failed',
            'timestamp': '2025-01-28T10:00:00',
            'full_line': '2025-01-28 10:00:00 - ERROR: Database connection failed'
        }
    ],
    'issues_found': 1
}
```

#### `diagnose_and_fix(issue: Dict, repo_path: Path) -> Dict`
Diagnose an issue and attempt automated fix.

**Parameters:**
- `issue`: Issue dict with 'type' and 'detail' keys
- `repo_path`: Path to repository

**Returns:**
```python
{
    'diagnosis': 'Connection timeout due to network latency',
    'fix_description': 'Increase timeout setting',
    'fix_commands': ['edit config.py', 'set timeout=30'],
    'confidence': 0.85,
    'prevention': 'Add connection pooling'
}
```

#### `think_about_improvements(stats: Dict, repo_path: Path) -> List[Dict]`
Analyze patterns and suggest proactive improvements.

**Returns:**
```python
[
    {
        'title': 'Add connection pooling',
        'description': 'Implement connection pooling to prevent timeout issues',
        'category': 'reliability',
        'priority': 80,
        'reasoning': 'Prevents recurring database timeout errors'
    }
]
```

#### `get_recent_logs(lines: int = 100) -> str`
Get recent log lines as a string.

#### `save_issues(issues: List[Dict])`
Save detected issues to `.selfai_data/issues.json`.

#### `save_improvements(improvements: List[Dict])`
Save suggested improvements to `.selfai_data/improvements.json`.

## How It Works

### Phase 5: Log Analysis (in SelfAIRunner.run())

1. **Analyze Logs**: Scan recent log file for error patterns
2. **Save Issues**: Persist detected issues to JSON file
3. **Auto-Diagnose Critical Issues**: For errors and exceptions:
   - Check pattern library for known fixes
   - If unknown, ask Claude for diagnosis
   - Learn from the diagnosis for future use
4. **Think About Improvements**: After 5+ completed tasks:
   - Analyze system health trends
   - Suggest proactive improvements
   - Add suggestions as new tasks

### Learning Process

1. **Issue Detected**: Error pattern matched in logs
2. **Diagnosis**: Claude analyzes and provides fix
3. **Learn**: Store pattern with confidence score
4. **Next Time**: Check pattern library first
5. **Success**: Increment success count, boost confidence
6. **Apply**: Auto-apply high-confidence fixes (>0.7)

### Similarity Matching

Uses SequenceMatcher for fuzzy pattern matching:
- Compares new issues against known patterns
- Threshold: 0.8 for pattern library lookup
- Threshold: 0.85 for pattern consolidation
- Case-insensitive comparison

## Performance Considerations

### Log Analysis Speed
- **10K lines**: ~1-2 seconds
- **100K lines**: ~5-10 seconds
- Compiled regex patterns for efficiency
- Runs asynchronously in Phase 5

### Pattern Library Growth
- Automatic pruning recommended after 1000 entries
- Consolidates similar patterns automatically
- Low-confidence patterns (<0.3) can be removed after 30 days

### Memory Usage
- Pattern library: ~1KB per pattern
- 1000 patterns ≈ 1MB
- Issues file: depends on log volume
- No in-memory caching of full logs

## Best Practices

### 1. Regular Monitoring
```bash
# Check for issues daily
python -m selfai analyze-logs
```

### 2. Review Diagnoses
Manually review low-confidence diagnoses before applying fixes:
```python
if diagnosis['confidence'] < 0.8:
    # Review before applying
    print(f"Review needed: {diagnosis['diagnosis']}")
```

### 3. Pattern Maintenance
Periodically review and clean the pattern library:
```python
patterns = analyzer._load_patterns()
# Remove low-confidence old patterns
active = [p for p in patterns if p['confidence'] > 0.3]
analyzer._save_patterns(active)
```

### 4. Custom Error Types
Add domain-specific error patterns:
```python
analyzer.error_patterns.extend([
    (r'PAYMENT_FAILED[:\s]+(.+)', 'payment_error'),
    (r'AUTH_ERROR[:\s]+(.+)', 'auth_error'),
])
```

## Troubleshooting

### Issue: No issues detected
- **Check log file exists**: `.selfai_data/logs/runner.log`
- **Verify log format**: Ensure timestamps and error keywords present
- **Adjust patterns**: Add custom patterns if needed

### Issue: Diagnosis fails
- **Check Claude CLI**: `claude --version`
- **Timeout**: Increase timeout in diagnose_and_fix (default 120s)
- **Permissions**: Ensure read access to repository files

### Issue: Pattern library not learning
- **Check file permissions**: `.selfai_data/patterns.json`
- **Verify JSON format**: Validate patterns file is not corrupted
- **Check confidence**: Ensure diagnoses return confidence scores

### Issue: Too many false positives
- **Refine patterns**: Make regex more specific
- **Ignore benign messages**: Add exclusion patterns
- **Adjust threshold**: Increase pattern matching threshold

## Examples

### Example 1: Detect and Fix Database Errors
```python
# First run - learns the pattern
analysis = analyzer.analyze_logs()
issue = analysis['issues'][0]  # Database connection error
diagnosis = analyzer.diagnose_and_fix(issue, repo_path)
# Claude diagnoses: "Increase timeout"

# Second run - applies known fix
analysis = analyzer.analyze_logs()
issue = analysis['issues'][0]  # Same error
known_fix = analyzer._check_pattern_library(issue)
# Returns stored fix immediately, no Claude call needed
```

### Example 2: Proactive Improvement Suggestions
```python
stats = {'completed': 10, 'failed': 2, 'pending': 5}
improvements = analyzer.think_about_improvements(stats, repo_path)
# Returns: [
#   {'title': 'Add retry logic', 'priority': 85},
#   {'title': 'Improve error handling', 'priority': 70}
# ]
```

### Example 3: Custom Pattern Detection
```python
# Add custom pattern for API rate limiting
analyzer.error_patterns.append(
    (r'Rate limit exceeded[:\s]+(.+)', 'rate_limit')
)

analysis = analyzer.analyze_logs()
# Now detects rate limit errors
```

## Future Enhancements

Potential improvements (not in current implementation):

1. **Machine Learning**: Use ML models for anomaly detection
2. **Correlation Analysis**: Link errors across multiple log files
3. **Real-time Monitoring**: Stream log analysis instead of batch
4. **Alert System**: Send notifications for critical issues
5. **Performance Metrics**: Track response times and resource usage
6. **Distributed Logging**: Analyze logs from multiple services
7. **Visualization**: Dashboard with error trends and charts

## Related Documentation

- [SelfAI Main README](../README.md)
- [Database Schema](../selfai/database.py)
- [Self-Healing Monitor](../selfai/monitoring.py)
- [Test Environment](../selfai/test_environment.py)
