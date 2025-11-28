"""Autonomous improvement discovery engine."""
import os
import subprocess
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger('selfai')


class DiscoveryCategory(Enum):
    SECURITY = 'security'
    TEST_COVERAGE = 'test_coverage'
    REFACTORING = 'refactoring'
    DOCUMENTATION = 'documentation'
    PERFORMANCE = 'performance'
    CODE_QUALITY = 'code_quality'


@dataclass
class DiscoveredImprovement:
    title: str
    description: str
    category: DiscoveryCategory
    priority: int  # 1-100, higher = more important
    confidence: float  # 0.0-1.0
    file_paths: List[str]
    metadata: Dict


class DiscoveryEngine:
    """Discovers potential improvements by analyzing the codebase."""

    def __init__(self, repo_path: Path, db):
        self.repo_path = repo_path
        self.db = db
        self.claude_cmd = os.environ.get('CLAUDE_CMD', 'claude')

    def discover_all(self, categories: List[DiscoveryCategory] = None) -> List[DiscoveredImprovement]:
        """Run all discovery scans and return found improvements."""
        if categories is None:
            categories = list(DiscoveryCategory)

        discoveries = []
        for category in categories:
            discovered = self._discover_category(category)
            discoveries.extend(discovered)

        # Deduplicate and prioritize
        return self._prioritize_discoveries(discoveries)

    def _discover_category(self, category: DiscoveryCategory) -> List[DiscoveredImprovement]:
        """Dispatch to category-specific discovery method."""
        dispatch = {
            DiscoveryCategory.SECURITY: self._discover_security,
            DiscoveryCategory.TEST_COVERAGE: self._discover_missing_tests,
            DiscoveryCategory.REFACTORING: self._discover_refactoring,
            DiscoveryCategory.DOCUMENTATION: self._discover_documentation,
            DiscoveryCategory.PERFORMANCE: self._discover_performance,
            DiscoveryCategory.CODE_QUALITY: self._discover_code_quality,
        }
        return dispatch[category]()

    def _discover_security(self) -> List[DiscoveredImprovement]:
        """Use Claude to analyze codebase for security vulnerabilities."""
        prompt = '''<task>Analyze this codebase for security vulnerabilities</task>
<focus>
- Hardcoded secrets, API keys, passwords
- SQL injection vulnerabilities
- Command injection risks (subprocess, os.system)
- Path traversal vulnerabilities
- Insecure deserialization
- Missing input validation
- XSS vulnerabilities in any web components
</focus>
<output_format>
Return JSON array of findings:
[
  {
    "title": "Brief title",
    "description": "Detailed description of the vulnerability",
    "severity": "critical|high|medium|low",
    "file_path": "path/to/file.py",
    "line_numbers": [10, 15],
    "recommendation": "How to fix"
  }
]
Return empty array [] if no issues found.
</output_format>'''
        return self._run_ai_discovery(prompt, DiscoveryCategory.SECURITY)

    def _discover_missing_tests(self) -> List[DiscoveredImprovement]:
        """Identify code lacking test coverage."""
        prompt = '''<task>Analyze this codebase to identify missing test coverage</task>
<focus>
- Public functions/methods without corresponding tests
- Complex logic branches that aren't tested
- Edge cases that should be tested
- Critical paths (error handling, auth, data validation)
- Integration points that need testing
</focus>
<output_format>
Return JSON array:
[
  {
    "title": "Add tests for [function/module]",
    "description": "What needs to be tested and why",
    "file_path": "path/to/file.py",
    "function_names": ["func1", "func2"],
    "test_suggestions": ["Test case 1", "Test case 2"]
  }
]
</output_format>'''
        return self._run_ai_discovery(prompt, DiscoveryCategory.TEST_COVERAGE)

    def _discover_refactoring(self) -> List[DiscoveredImprovement]:
        """Identify refactoring opportunities."""
        prompt = '''<task>Analyze this codebase for refactoring opportunities</task>
<focus>
- Long methods (>50 lines) that should be broken down
- Duplicated code that should be extracted
- Complex nested conditionals
- God classes with too many responsibilities
- Poor naming conventions
- Tightly coupled components
- Outdated patterns that could use modern alternatives
</focus>
<output_format>
Return JSON array:
[
  {
    "title": "Refactor [what]",
    "description": "Current problem and proposed solution",
    "file_path": "path/to/file.py",
    "complexity": "low|medium|high",
    "benefit": "Readability/Maintainability/Performance"
  }
]
</output_format>'''
        return self._run_ai_discovery(prompt, DiscoveryCategory.REFACTORING)

    def _discover_documentation(self) -> List[DiscoveredImprovement]:
        """Find documentation gaps."""
        prompt = '''<task>Analyze this codebase for documentation gaps</task>
<focus>
- Public APIs without docstrings
- Complex functions lacking explanations
- Missing module-level documentation
- Outdated or incorrect comments
- Missing type hints on public interfaces
- Undocumented configuration options
</focus>
<output_format>
Return JSON array:
[
  {
    "title": "Document [what]",
    "description": "What documentation is missing",
    "file_path": "path/to/file.py",
    "items": ["function1", "class2"]
  }
]
</output_format>'''
        return self._run_ai_discovery(prompt, DiscoveryCategory.DOCUMENTATION)

    def _discover_performance(self) -> List[DiscoveredImprovement]:
        """Identify performance issues."""
        prompt = '''<task>Analyze this codebase for performance issues</task>
<focus>
- N+1 query patterns
- Inefficient loops (O(n^2) when O(n) is possible)
- Unnecessary object creation in hot paths
- Missing caching opportunities
- Blocking I/O that could be async
- Large data structures loaded into memory
</focus>
<output_format>
Return JSON array:
[
  {
    "title": "Optimize [what]",
    "description": "Current problem and optimization suggestion",
    "file_path": "path/to/file.py",
    "impact": "low|medium|high"
  }
]
</output_format>'''
        return self._run_ai_discovery(prompt, DiscoveryCategory.PERFORMANCE)

    def _discover_code_quality(self) -> List[DiscoveredImprovement]:
        """Find general code quality issues."""
        prompt = '''<task>Analyze this codebase for code quality issues</task>
<focus>
- Unused imports and variables
- Dead code paths
- Magic numbers without constants
- Inconsistent error handling
- Missing error handling
- Violation of DRY principle
- Overly complex conditionals
</focus>
<output_format>
Return JSON array:
[
  {
    "title": "Fix [what]",
    "description": "The issue and how to fix it",
    "file_path": "path/to/file.py",
    "severity": "low|medium|high"
  }
]
</output_format>'''
        return self._run_ai_discovery(prompt, DiscoveryCategory.CODE_QUALITY)

    def _run_ai_discovery(self, prompt: str, category: DiscoveryCategory) -> List[DiscoveredImprovement]:
        """Run Claude to discover improvements of a specific category."""
        try:
            result = subprocess.run(
                [self.claude_cmd, '-p', prompt, '--allowedTools', 'Read,Glob,Grep'],
                capture_output=True,
                text=True,
                timeout=180,  # 3 minute timeout per category
                cwd=str(self.repo_path)
            )

            if result.returncode != 0:
                logger.warning(f"Discovery failed for {category.value}: {result.stderr}")
                return []

            return self._parse_discovery_output(result.stdout, category)

        except subprocess.TimeoutExpired:
            logger.warning(f"Discovery timed out for {category.value}")
            return []
        except Exception as e:
            logger.error(f"Discovery error for {category.value}: {e}")
            return []

    def _parse_discovery_output(self, output: str, category: DiscoveryCategory) -> List[DiscoveredImprovement]:
        """Parse Claude's JSON output into DiscoveredImprovement objects."""
        try:
            # Extract JSON from output (may have surrounding text)
            json_match = re.search(r'\[\s*\{.*\}\s*\]', output, re.DOTALL)
            if not json_match:
                return []

            findings = json.loads(json_match.group())
            improvements = []

            for finding in findings:
                priority = self._calculate_priority(finding, category)
                improvements.append(DiscoveredImprovement(
                    title=finding.get('title', 'Untitled improvement'),
                    description=finding.get('description', ''),
                    category=category,
                    priority=priority,
                    confidence=finding.get('confidence', 0.7),
                    file_paths=[finding.get('file_path', '')] if 'file_path' in finding else [],
                    metadata=finding
                ))

            return improvements
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse discovery output: {e}")
            return []

    def _calculate_priority(self, finding: Dict, category: DiscoveryCategory) -> int:
        """Calculate priority score (1-100) based on category and severity."""
        # Base priority by category
        category_base = {
            DiscoveryCategory.SECURITY: 80,
            DiscoveryCategory.TEST_COVERAGE: 60,
            DiscoveryCategory.REFACTORING: 40,
            DiscoveryCategory.DOCUMENTATION: 30,
            DiscoveryCategory.PERFORMANCE: 50,
            DiscoveryCategory.CODE_QUALITY: 45,
        }

        base = category_base.get(category, 50)

        # Adjust by severity if present
        severity_mod = {
            'critical': 20,
            'high': 10,
            'medium': 0,
            'low': -10
        }
        severity = finding.get('severity', 'medium').lower()
        modifier = severity_mod.get(severity, 0)

        return max(1, min(100, base + modifier))

    def _prioritize_discoveries(self, discoveries: List[DiscoveredImprovement]) -> List[DiscoveredImprovement]:
        """Deduplicate and sort discoveries by priority."""
        # Remove duplicates based on title similarity
        seen_titles = set()
        unique = []
        for d in discoveries:
            normalized_title = d.title.lower().strip()
            if normalized_title not in seen_titles:
                seen_titles.add(normalized_title)
                unique.append(d)

        # Sort by priority (highest first)
        return sorted(unique, key=lambda x: x.priority, reverse=True)

    def _filter_existing(self, discoveries: List[DiscoveredImprovement]) -> List[DiscoveredImprovement]:
        """Filter out discoveries that already exist in database."""
        return [d for d in discoveries if not self.db.exists(d.title)]
