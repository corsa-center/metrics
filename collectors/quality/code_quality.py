"""
Code Quality Collector

Analyzes repositories for code quality indicators including:
- Code duplication detection
- Design pattern recognition
- Architectural consistency
- Technical debt indicators
- Complexity metrics (cyclomatic, cognitive, nesting, maintainability)

Metrics Covered:
- duplication_score: Measures code repetition (lower is better)
- design_patterns_score: Recognition of common design patterns
- architectural_consistency_score: Consistency in project structure and conventions
- technical_debt_score: Indicators of accumulated technical debt
- complexity_score: Cyclomatic/cognitive complexity and maintainability indices
"""

import asyncio
import logging
import re
import math
from typing import Dict, Any, Optional, List, Tuple, Set
from datetime import datetime
from collections import defaultdict
import httpx

logger = logging.getLogger(__name__)


class CodeQualityCollector:
    """
    Collects code quality metrics by analyzing repository structure,
    code patterns, and technical debt indicators.
    """

    # File extensions to analyze by language
    LANGUAGE_EXTENSIONS = {
        "python": [".py"],
        "javascript": [".js", ".jsx", ".mjs"],
        "typescript": [".ts", ".tsx"],
        "java": [".java"],
        "go": [".go"],
        "rust": [".rs"],
        "cpp": [".cpp", ".cc", ".cxx", ".hpp", ".h"],
        "csharp": [".cs"],
        "ruby": [".rb"],
    }

    # Design pattern indicators by language
    DESIGN_PATTERNS = {
        "singleton": {
            "python": [r"_instance\s*=\s*None", r"def\s+get_instance", r"__new__\s*\("],
            "javascript": [r"static\s+getInstance", r"let\s+instance\s*=\s*null"],
            "typescript": [r"private\s+static\s+instance", r"static\s+getInstance"],
            "java": [r"private\s+static\s+\w+\s+instance", r"getInstance\s*\(\)"],
        },
        "factory": {
            "python": [r"def\s+create_\w+", r"class\s+\w+Factory", r"@staticmethod\s+def\s+create"],
            "javascript": [r"function\s+create\w+", r"class\s+\w+Factory"],
            "typescript": [r"static\s+create", r"class\s+\w+Factory"],
            "java": [r"public\s+static\s+\w+\s+create", r"class\s+\w+Factory"],
        },
        "observer": {
            "python": [r"def\s+subscribe", r"def\s+notify", r"self\._observers", r"def\s+add_listener"],
            "javascript": [r"addEventListener", r"on\w+\s*=", r"emit\(", r"subscribe\("],
            "typescript": [r"Observable", r"Subject", r"subscribe\(", r"emit\("],
            "java": [r"interface\s+\w*Observer", r"void\s+update\(", r"notifyObservers"],
        },
        "strategy": {
            "python": [r"class\s+\w+Strategy", r"def\s+set_strategy", r"self\._strategy"],
            "javascript": [r"setStrategy", r"class\s+\w+Strategy"],
            "typescript": [r"interface\s+\w+Strategy", r"setStrategy"],
            "java": [r"interface\s+\w+Strategy", r"void\s+setStrategy"],
        },
        "decorator": {
            "python": [r"@\w+", r"def\s+__call__\s*\(self", r"functools\.wraps"],
            "javascript": [r"@\w+", r"function\s+\w+Decorator"],
            "typescript": [r"@\w+\(", r"function\s+\w+Decorator"],
            "java": [r"@\w+", r"class\s+\w+Decorator\s+extends"],
        },
        "dependency_injection": {
            "python": [r"def\s+__init__\s*\(\s*self\s*,\s*\w+:", r"@inject", r"@Inject"],
            "javascript": [r"constructor\s*\(\s*\w+\s*\)", r"@Inject"],
            "typescript": [r"constructor\s*\(\s*private", r"@Injectable", r"@Inject"],
            "java": [r"@Inject", r"@Autowired", r"@Resource"],
        },
        "repository": {
            "python": [r"class\s+\w+Repository", r"def\s+find_by", r"def\s+save\("],
            "javascript": [r"class\s+\w+Repository", r"findBy\w+", r"async\s+save"],
            "typescript": [r"interface\s+\w+Repository", r"findBy\w+", r"Repository<"],
            "java": [r"interface\s+\w+Repository", r"@Repository", r"JpaRepository"],
        },
    }

    # Technical debt indicators
    DEBT_INDICATORS = {
        "todo_comments": [r"#\s*TODO", r"//\s*TODO", r"/\*\s*TODO", r"#\s*FIXME", r"//\s*FIXME"],
        "hack_comments": [r"#\s*HACK", r"//\s*HACK", r"#\s*XXX", r"//\s*XXX", r"#\s*KLUDGE"],
        "deprecated_markers": [r"@deprecated", r"@Deprecated", r"# deprecated", r"// deprecated"],
        "magic_numbers": [r"(?<!\w)(?<!\.)\b(?!0\b|1\b|2\b)([3-9]|\d{2,})\b(?!\.\d)(?!\s*[=<>])"],
        "long_methods": [],  # Detected by line counting
        "deep_nesting": [],  # Detected by indentation analysis
        "commented_code": [r"^\s*#\s*(def |class |import |from )", r"^\s*//\s*(function |class |const |let |var )"],
        "hardcoded_credentials": [
            r"password\s*=\s*['\"][^'\"]+['\"]",
            r"api_key\s*=\s*['\"][^'\"]+['\"]",
            r"secret\s*=\s*['\"][^'\"]+['\"]",
        ],
    }

    # Architectural patterns to check
    ARCHITECTURE_INDICATORS = {
        "layered": {
            "dirs": ["controllers", "services", "repositories", "models", "views"],
            "patterns": [r"controller", r"service", r"repository", r"model"],
        },
        "mvc": {
            "dirs": ["models", "views", "controllers"],
            "patterns": [r"Model", r"View", r"Controller"],
        },
        "clean_architecture": {
            "dirs": ["domain", "application", "infrastructure", "presentation", "entities", "usecases"],
            "patterns": [r"UseCase", r"Repository", r"Entity", r"Gateway"],
        },
        "hexagonal": {
            "dirs": ["adapters", "ports", "domain", "application"],
            "patterns": [r"Port", r"Adapter", r"UseCase"],
        },
        "microservices": {
            "dirs": ["services", "api", "gateway", "common"],
            "patterns": [r"Service", r"API", r"Gateway", r"Client"],
        },
    }

    def __init__(self, github_token: Optional[str] = None):
        """Initialize the collector with optional GitHub token."""
        self.github_token = github_token
        self.github_headers = {}
        if github_token:
            self.github_headers = {
                "Authorization": f"token {github_token}",
                "Accept": "application/vnd.github.v3+json",
            }
        self._file_cache: Dict[str, str] = {}

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        """
        Main entry point - collects all code quality metrics for a package.

        Args:
            package: Dictionary containing package info with 'repo_url' key

        Returns:
            Dictionary with all collected metrics and scores
        """
        repo_url = package.get("repo_url", "")
        repo_name = package.get("name", "unknown")

        logger.info(f"Collecting code quality metrics for {repo_name}")

        owner_repo = self._extract_owner_repo(repo_url)
        if not owner_repo:
            logger.warning(f"Could not extract owner/repo from URL: {repo_url}")
            return self._empty_result(repo_name)

        owner, repo = owner_repo

        try:
            # Collect all metrics concurrently
            results = await asyncio.gather(
                self._analyze_duplication(owner, repo),
                self._detect_design_patterns(owner, repo),
                self._check_architectural_consistency(owner, repo),
                self._assess_technical_debt(owner, repo),
                self._analyze_complexity_metrics(owner, repo),
                return_exceptions=True,
            )

            # Handle any exceptions in results
            duplication = results[0] if not isinstance(results[0], Exception) else self._empty_duplication()
            patterns = results[1] if not isinstance(results[1], Exception) else self._empty_patterns()
            architecture = results[2] if not isinstance(results[2], Exception) else self._empty_architecture()
            debt = results[3] if not isinstance(results[3], Exception) else self._empty_debt()
            complexity = results[4] if not isinstance(results[4], Exception) else self._empty_complexity()

            # Log any exceptions
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"Error in metric collection {i}: {result}")

            # Calculate overall score
            overall_score = self._calculate_overall_score(
                duplication, patterns, architecture, debt, complexity
            )

            return {
                "package_name": repo_name,
                "repository": f"{owner}/{repo}",
                "timestamp": self._get_timestamp(),
                "duplication": duplication,
                "design_patterns": patterns,
                "architectural_consistency": architecture,
                "technical_debt": debt,
                "complexity": complexity,
                "overall_score": overall_score,
            }

        except Exception as e:
            logger.error(f"Error collecting metrics for {repo_name}: {e}")
            return self._empty_result(repo_name)

    async def _analyze_duplication(self, owner: str, repo: str) -> Dict[str, Any]:
        """
        Analyze code duplication using content hashing and similarity detection.

        Returns metrics on:
        - Duplicate file detection
        - Similar code block identification
        - Copy-paste pattern detection
        """
        logger.debug(f"Analyzing duplication for {owner}/{repo}")

        try:
            # Get repository tree
            tree = await self._get_repo_tree(owner, repo)
            if not tree:
                return self._empty_duplication()

            # Filter to code files only
            code_files = self._filter_code_files(tree)
            total_files = len(code_files)

            if total_files == 0:
                return self._empty_duplication()

            # Sample files for analysis (limit to avoid rate limits)
            sample_size = min(50, total_files)
            sampled_files = code_files[:sample_size]

            # Fetch file contents
            file_contents = await self._fetch_file_contents(owner, repo, sampled_files)

            # Analyze for duplication
            duplicate_blocks = self._find_duplicate_blocks(file_contents)
            similar_files = self._find_similar_files(file_contents)

            # Calculate duplication metrics
            total_lines = sum(len(content.split("\n")) for content in file_contents.values())
            duplicated_lines = sum(block["lines"] for block in duplicate_blocks)

            duplication_ratio = duplicated_lines / max(total_lines, 1)
            duplication_score = max(0, 100 - (duplication_ratio * 200))  # Penalize duplication

            return {
                "score": round(duplication_score, 2),
                "total_files_analyzed": len(file_contents),
                "total_lines": total_lines,
                "duplicated_lines": duplicated_lines,
                "duplication_ratio": round(duplication_ratio, 4),
                "duplicate_blocks": len(duplicate_blocks),
                "similar_file_pairs": len(similar_files),
                "details": {
                    "top_duplicates": duplicate_blocks[:5],
                    "similar_files": similar_files[:5],
                },
                "status": self._get_status(duplication_score),
            }

        except Exception as e:
            logger.error(f"Error analyzing duplication: {e}")
            return self._empty_duplication()

    async def _detect_design_patterns(self, owner: str, repo: str) -> Dict[str, Any]:
        """
        Detect usage of common design patterns in the codebase.

        Looks for:
        - Singleton, Factory, Observer, Strategy patterns
        - Decorator usage
        - Dependency injection
        - Repository pattern
        """
        logger.debug(f"Detecting design patterns for {owner}/{repo}")

        try:
            # Get repository tree
            tree = await self._get_repo_tree(owner, repo)
            if not tree:
                return self._empty_patterns()

            # Determine primary language
            language = await self._detect_primary_language(owner, repo)

            # Filter to code files
            code_files = self._filter_code_files(tree, language)
            sample_files = code_files[:30]

            # Fetch contents
            file_contents = await self._fetch_file_contents(owner, repo, sample_files)

            # Detect patterns
            detected_patterns: Dict[str, List[Dict]] = defaultdict(list)

            for pattern_name, language_patterns in self.DESIGN_PATTERNS.items():
                patterns = language_patterns.get(language, language_patterns.get("python", []))
                for filepath, content in file_contents.items():
                    for pattern in patterns:
                        matches = re.findall(pattern, content, re.MULTILINE)
                        if matches:
                            detected_patterns[pattern_name].append({
                                "file": filepath,
                                "matches": len(matches),
                            })

            # Calculate pattern diversity score
            unique_patterns = len(detected_patterns)
            total_possible = len(self.DESIGN_PATTERNS)
            pattern_coverage = unique_patterns / total_possible

            # Bonus for consistent pattern usage
            consistency_bonus = min(20, sum(
                5 for p, files in detected_patterns.items() if len(files) >= 2
            ))

            score = min(100, (pattern_coverage * 80) + consistency_bonus)

            return {
                "score": round(score, 2),
                "patterns_detected": unique_patterns,
                "total_patterns_checked": total_possible,
                "pattern_coverage": round(pattern_coverage, 2),
                "primary_language": language,
                "patterns": {
                    name: {
                        "occurrences": len(files),
                        "files": [f["file"] for f in files[:3]],
                    }
                    for name, files in detected_patterns.items()
                },
                "status": self._get_status(score),
            }

        except Exception as e:
            logger.error(f"Error detecting design patterns: {e}")
            return self._empty_patterns()

    async def _check_architectural_consistency(self, owner: str, repo: str) -> Dict[str, Any]:
        """
        Check for architectural consistency in the repository structure.

        Evaluates:
        - Directory structure patterns (MVC, layered, clean architecture)
        - Naming conventions consistency
        - Module organization
        - Separation of concerns indicators
        """
        logger.debug(f"Checking architectural consistency for {owner}/{repo}")

        try:
            # Get repository tree
            tree = await self._get_repo_tree(owner, repo)
            if not tree:
                return self._empty_architecture()

            # Extract directory structure
            directories = self._extract_directories(tree)
            all_paths = [item["path"] for item in tree]

            # Detect architecture pattern
            detected_architecture = self._detect_architecture_pattern(directories)

            # Check naming conventions
            naming_consistency = self._check_naming_consistency(tree)

            # Check module organization
            module_organization = self._check_module_organization(tree)

            # Check for config separation
            config_separation = self._check_config_separation(all_paths)

            # Check for test organization
            test_organization = self._check_test_organization(all_paths)

            # Calculate scores
            arch_score = detected_architecture.get("confidence", 0) * 30
            naming_score = naming_consistency.get("score", 0) * 0.25
            module_score = module_organization.get("score", 0) * 0.20
            config_score = 15 if config_separation else 0
            test_score = 10 if test_organization else 0

            total_score = min(100, arch_score + naming_score + module_score + config_score + test_score)

            return {
                "score": round(total_score, 2),
                "detected_architecture": detected_architecture,
                "naming_consistency": naming_consistency,
                "module_organization": module_organization,
                "has_config_separation": config_separation,
                "has_test_organization": test_organization,
                "directory_count": len(directories),
                "status": self._get_status(total_score),
            }

        except Exception as e:
            logger.error(f"Error checking architecture: {e}")
            return self._empty_architecture()

    async def _assess_technical_debt(self, owner: str, repo: str) -> Dict[str, Any]:
        """
        Assess technical debt indicators in the repository.

        Checks for:
        - TODO/FIXME/HACK comments
        - Deprecated code markers
        - Long methods
        - Deep nesting
        - Commented-out code
        - Hardcoded values
        """
        logger.debug(f"Assessing technical debt for {owner}/{repo}")

        try:
            # Get repository tree
            tree = await self._get_repo_tree(owner, repo)
            if not tree:
                return self._empty_debt()

            # Filter to code files
            code_files = self._filter_code_files(tree)
            sample_files = code_files[:40]

            # Fetch contents
            file_contents = await self._fetch_file_contents(owner, repo, sample_files)

            # Initialize counters
            debt_indicators: Dict[str, List[Dict]] = defaultdict(list)
            total_lines = 0

            for filepath, content in file_contents.items():
                lines = content.split("\n")
                total_lines += len(lines)

                # Check for comment-based indicators
                for indicator_name, patterns in self.DEBT_INDICATORS.items():
                    if not patterns:  # Skip structural indicators
                        continue
                    for pattern in patterns:
                        for i, line in enumerate(lines):
                            if re.search(pattern, line, re.IGNORECASE):
                                debt_indicators[indicator_name].append({
                                    "file": filepath,
                                    "line": i + 1,
                                    "content": line.strip()[:100],
                                })

                # Check for long methods
                long_methods = self._find_long_methods(content, filepath)
                debt_indicators["long_methods"].extend(long_methods)

                # Check for deep nesting
                deep_nesting = self._find_deep_nesting(content, filepath)
                debt_indicators["deep_nesting"].extend(deep_nesting)

            # Calculate debt score (lower debt = higher score)
            total_issues = sum(len(issues) for issues in debt_indicators.values())
            issues_per_1k_lines = (total_issues / max(total_lines, 1)) * 1000

            # Weighted scoring
            weights = {
                "todo_comments": 1,
                "hack_comments": 3,
                "deprecated_markers": 2,
                "magic_numbers": 0.5,
                "long_methods": 4,
                "deep_nesting": 3,
                "commented_code": 2,
                "hardcoded_credentials": 10,
            }

            weighted_score = sum(
                len(issues) * weights.get(indicator, 1)
                for indicator, issues in debt_indicators.items()
            )

            # Normalize to 0-100 (higher is better = less debt)
            debt_density = weighted_score / max(total_lines / 100, 1)
            score = max(0, 100 - min(100, debt_density * 5))

            return {
                "score": round(score, 2),
                "total_issues": total_issues,
                "total_lines_analyzed": total_lines,
                "issues_per_1k_lines": round(issues_per_1k_lines, 2),
                "breakdown": {
                    indicator: {
                        "count": len(issues),
                        "samples": [
                            {"file": i["file"], "line": i.get("line", 0)}
                            for i in issues[:3]
                        ],
                    }
                    for indicator, issues in debt_indicators.items()
                    if issues
                },
                "priority_issues": self._prioritize_debt_issues(debt_indicators),
                "status": self._get_status(score),
            }

        except Exception as e:
            logger.error(f"Error assessing technical debt: {e}")
            return self._empty_debt()

    async def _analyze_complexity_metrics(self, owner: str, repo: str) -> Dict[str, Any]:
        """
        Analyze code complexity metrics.

        Calculates:
        - Cyclomatic complexity: Number of linearly independent paths
        - Cognitive complexity: How difficult code is to understand
        - Nesting depth: Maximum and average nesting levels
        - Maintainability index: Composite maintainability score
        """
        logger.debug(f"Analyzing complexity metrics for {owner}/{repo}")

        try:
            # Get repository tree
            tree = await self._get_repo_tree(owner, repo)
            if not tree:
                return self._empty_complexity()

            # Filter to code files
            code_files = self._filter_code_files(tree)
            sample_files = code_files[:40]

            # Fetch contents
            file_contents = await self._fetch_file_contents(owner, repo, sample_files)

            if not file_contents:
                return self._empty_complexity()

            # Analyze each file
            all_functions: List[Dict] = []
            file_metrics: Dict[str, Dict] = {}

            for filepath, content in file_contents.items():
                language = self._detect_language(filepath)
                functions = self._extract_functions(content, language, filepath)

                file_cyclomatic = []
                file_cognitive = []
                file_nesting = []

                for func in functions:
                    # Calculate cyclomatic complexity
                    cyclomatic = self._calculate_cyclomatic_complexity(
                        func["body"], language
                    )
                    func["cyclomatic_complexity"] = cyclomatic
                    file_cyclomatic.append(cyclomatic)

                    # Calculate cognitive complexity
                    cognitive = self._calculate_cognitive_complexity(
                        func["body"], language
                    )
                    func["cognitive_complexity"] = cognitive
                    file_cognitive.append(cognitive)

                    # Calculate max nesting depth
                    max_nesting = self._calculate_max_nesting(func["body"], language)
                    func["max_nesting"] = max_nesting
                    file_nesting.append(max_nesting)

                    # Calculate maintainability index for function
                    func["maintainability_index"] = self._calculate_maintainability_index(
                        func["body"], cyclomatic
                    )

                    all_functions.append(func)

                # File-level metrics
                if file_cyclomatic:
                    file_metrics[filepath] = {
                        "avg_cyclomatic": round(sum(file_cyclomatic) / len(file_cyclomatic), 2),
                        "max_cyclomatic": max(file_cyclomatic),
                        "avg_cognitive": round(sum(file_cognitive) / len(file_cognitive), 2),
                        "max_cognitive": max(file_cognitive),
                        "avg_nesting": round(sum(file_nesting) / len(file_nesting), 2),
                        "max_nesting": max(file_nesting),
                        "function_count": len(file_cyclomatic),
                    }

            # Calculate aggregate metrics
            if not all_functions:
                return self._empty_complexity()

            all_cyclomatic = [f["cyclomatic_complexity"] for f in all_functions]
            all_cognitive = [f["cognitive_complexity"] for f in all_functions]
            all_nesting = [f["max_nesting"] for f in all_functions]
            all_maintainability = [f["maintainability_index"] for f in all_functions]

            # Find problematic functions
            high_complexity_functions = [
                {
                    "name": f["name"],
                    "file": f["file"],
                    "line": f["line"],
                    "cyclomatic": f["cyclomatic_complexity"],
                    "cognitive": f["cognitive_complexity"],
                }
                for f in all_functions
                if f["cyclomatic_complexity"] > 10 or f["cognitive_complexity"] > 15
            ]
            high_complexity_functions.sort(
                key=lambda x: x["cyclomatic"] + x["cognitive"], reverse=True
            )

            deeply_nested_functions = [
                {
                    "name": f["name"],
                    "file": f["file"],
                    "line": f["line"],
                    "max_nesting": f["max_nesting"],
                }
                for f in all_functions
                if f["max_nesting"] > 4
            ]
            deeply_nested_functions.sort(key=lambda x: x["max_nesting"], reverse=True)

            # Calculate scores (lower complexity = higher score)
            avg_cyclomatic = sum(all_cyclomatic) / len(all_cyclomatic)
            avg_cognitive = sum(all_cognitive) / len(all_cognitive)
            avg_nesting = sum(all_nesting) / len(all_nesting)
            avg_maintainability = sum(all_maintainability) / len(all_maintainability)

            # Score calculations
            cyclomatic_score = self._score_cyclomatic(avg_cyclomatic)
            cognitive_score = self._score_cognitive(avg_cognitive)
            nesting_score = self._score_nesting(avg_nesting)
            maintainability_score = min(100, avg_maintainability)

            # Weighted overall complexity score
            overall_score = (
                cyclomatic_score * 0.30 +
                cognitive_score * 0.30 +
                nesting_score * 0.15 +
                maintainability_score * 0.25
            )

            return {
                "score": round(overall_score, 2),
                "cyclomatic_complexity": {
                    "average": round(avg_cyclomatic, 2),
                    "max": max(all_cyclomatic),
                    "median": round(sorted(all_cyclomatic)[len(all_cyclomatic) // 2], 2),
                    "score": round(cyclomatic_score, 2),
                    "thresholds": {
                        "low": "1-5",
                        "moderate": "6-10",
                        "high": "11-20",
                        "very_high": ">20",
                    },
                },
                "cognitive_complexity": {
                    "average": round(avg_cognitive, 2),
                    "max": max(all_cognitive),
                    "median": round(sorted(all_cognitive)[len(all_cognitive) // 2], 2),
                    "score": round(cognitive_score, 2),
                    "thresholds": {
                        "low": "1-8",
                        "moderate": "9-15",
                        "high": "16-25",
                        "very_high": ">25",
                    },
                },
                "nesting_depth": {
                    "average": round(avg_nesting, 2),
                    "max": max(all_nesting),
                    "score": round(nesting_score, 2),
                    "thresholds": {
                        "good": "1-3",
                        "acceptable": "4",
                        "concerning": "5-6",
                        "problematic": ">6",
                    },
                },
                "maintainability_index": {
                    "average": round(avg_maintainability, 2),
                    "min": round(min(all_maintainability), 2),
                    "score": round(maintainability_score, 2),
                    "interpretation": self._interpret_maintainability(avg_maintainability),
                    "scale": "0-100 (higher is better)",
                },
                "functions_analyzed": len(all_functions),
                "files_analyzed": len(file_metrics),
                "high_complexity_functions": high_complexity_functions[:10],
                "deeply_nested_functions": deeply_nested_functions[:10],
                "by_file": dict(list(file_metrics.items())[:10]),
                "status": self._get_status(overall_score),
            }

        except Exception as e:
            logger.error(f"Error analyzing complexity: {e}")
            return self._empty_complexity()

    def _extract_functions(
        self, content: str, language: str, filepath: str
    ) -> List[Dict]:
        """Extract functions/methods from source code."""
        functions = []
        lines = content.split("\n")

        # Language-specific function patterns
        patterns = {
            "python": r"^\s*(async\s+)?def\s+(\w+)\s*\(",
            "javascript": r"(?:^|\s)(?:async\s+)?function\s+(\w+)\s*\(|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(",
            "typescript": r"(?:^|\s)(?:async\s+)?function\s+(\w+)\s*\(|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(|(?:public|private|protected)?\s*(?:async\s+)?(\w+)\s*\(",
            "java": r"(?:public|private|protected)\s+(?:static\s+)?(?:\w+(?:<[^>]+>)?)\s+(\w+)\s*\(",
            "go": r"func\s+(?:\([^)]+\)\s+)?(\w+)\s*\(",
            "rust": r"(?:pub\s+)?(?:async\s+)?fn\s+(\w+)",
            "cpp": r"(?:\w+(?:<[^>]+>)?\s+)+(\w+)\s*\([^)]*\)\s*(?:const)?\s*\{",
            "csharp": r"(?:public|private|protected|internal)\s+(?:static\s+)?(?:async\s+)?(?:\w+(?:<[^>]+>)?)\s+(\w+)\s*\(",
            "ruby": r"def\s+(\w+)",
        }

        pattern = patterns.get(language, patterns.get("python"))
        if not pattern:
            return functions

        current_func = None
        current_start = 0
        brace_count = 0
        in_function = False

        for i, line in enumerate(lines):
            match = re.search(pattern, line)
            if match:
                # Save previous function if exists
                if current_func and in_function:
                    func_body = "\n".join(lines[current_start:i])
                    functions.append({
                        "name": current_func,
                        "file": filepath,
                        "line": current_start + 1,
                        "body": func_body,
                        "length": i - current_start,
                    })

                # Start new function
                current_func = next((g for g in match.groups() if g), "anonymous")
                current_start = i
                in_function = True
                brace_count = line.count("{") - line.count("}")

            elif in_function:
                brace_count += line.count("{") - line.count("}")

                # For Python/Ruby, use indentation
                if language in ["python", "ruby"]:
                    if line.strip() and not line.startswith(" ") and not line.startswith("\t"):
                        if i > current_start:
                            func_body = "\n".join(lines[current_start:i])
                            functions.append({
                                "name": current_func,
                                "file": filepath,
                                "line": current_start + 1,
                                "body": func_body,
                                "length": i - current_start,
                            })
                            in_function = False
                            current_func = None

        # Handle last function
        if current_func and in_function:
            func_body = "\n".join(lines[current_start:])
            functions.append({
                "name": current_func,
                "file": filepath,
                "line": current_start + 1,
                "body": func_body,
                "length": len(lines) - current_start,
            })

        return functions

    def _calculate_cyclomatic_complexity(self, code: str, language: str) -> int:
        """
        Calculate cyclomatic complexity (McCabe complexity).

        CC = E - N + 2P where:
        - E = edges in control flow graph
        - N = nodes in control flow graph
        - P = connected components (usually 1)

        Simplified: CC = 1 + decision points
        """
        complexity = 1  # Base complexity

        # Decision point patterns by language
        decision_patterns = {
            "python": [
                r"\bif\b", r"\belif\b", r"\bfor\b", r"\bwhile\b",
                r"\band\b", r"\bor\b", r"\bexcept\b", r"\bwith\b",
                r"\bcase\b",  # match/case in Python 3.10+
            ],
            "javascript": [
                r"\bif\s*\(", r"\belse\s+if\b", r"\bfor\s*\(", r"\bwhile\s*\(",
                r"\bcase\b", r"\bcatch\b", r"\?\s*[^:]+\s*:",  # ternary
                r"&&", r"\|\|", r"\?\?",  # logical operators
            ],
            "typescript": [
                r"\bif\s*\(", r"\belse\s+if\b", r"\bfor\s*\(", r"\bwhile\s*\(",
                r"\bcase\b", r"\bcatch\b", r"\?\s*[^:]+\s*:",
                r"&&", r"\|\|", r"\?\?",
            ],
            "java": [
                r"\bif\s*\(", r"\belse\s+if\b", r"\bfor\s*\(", r"\bwhile\s*\(",
                r"\bcase\b", r"\bcatch\b", r"\?\s*[^:]+\s*:",
                r"&&", r"\|\|",
            ],
            "go": [
                r"\bif\b", r"\belse\s+if\b", r"\bfor\b", r"\bcase\b",
                r"&&", r"\|\|",
            ],
            "rust": [
                r"\bif\b", r"\belse\s+if\b", r"\bfor\b", r"\bwhile\b",
                r"\bmatch\b", r"=>",  # match arms
                r"&&", r"\|\|", r"\?",  # ? operator
            ],
            "cpp": [
                r"\bif\s*\(", r"\belse\s+if\b", r"\bfor\s*\(", r"\bwhile\s*\(",
                r"\bcase\b", r"\bcatch\b", r"\?\s*[^:]+\s*:",
                r"&&", r"\|\|",
            ],
            "csharp": [
                r"\bif\s*\(", r"\belse\s+if\b", r"\bfor\s*\(", r"\bforeach\s*\(",
                r"\bwhile\s*\(", r"\bcase\b", r"\bcatch\b", r"\?\s*[^:]+\s*:",
                r"&&", r"\|\|", r"\?\?",
            ],
            "ruby": [
                r"\bif\b", r"\belsif\b", r"\bunless\b", r"\bwhile\b",
                r"\buntil\b", r"\bfor\b", r"\bwhen\b", r"\brescue\b",
                r"\band\b", r"\bor\b",
            ],
        }

        patterns = decision_patterns.get(language, decision_patterns.get("python", []))

        for pattern in patterns:
            complexity += len(re.findall(pattern, code))

        return complexity

    def _calculate_cognitive_complexity(self, code: str, language: str) -> int:
        """
        Calculate cognitive complexity (SonarSource metric).

        Cognitive complexity measures how difficult code is to understand,
        penalizing nested structures more heavily than flat ones.
        """
        complexity = 0
        nesting_level = 0
        lines = code.split("\n")

        # Patterns that increase nesting
        nesting_increase = {
            "python": [r"^\s*(if|elif|for|while|with|try|except)\b", r"^\s*def\s+", r"^\s*class\s+"],
            "javascript": [r"\b(if|for|while|switch|try|catch)\s*\(", r"function\s*\(", r"=>\s*\{"],
            "typescript": [r"\b(if|for|while|switch|try|catch)\s*\(", r"function\s*\(", r"=>\s*\{"],
            "java": [r"\b(if|for|while|switch|try|catch)\s*\(", r"\bclass\s+"],
            "go": [r"\b(if|for|switch|select)\b", r"func\s+"],
            "rust": [r"\b(if|for|while|match|loop)\b", r"fn\s+"],
            "cpp": [r"\b(if|for|while|switch|try|catch)\s*\(", r"class\s+"],
            "csharp": [r"\b(if|for|foreach|while|switch|try|catch)\s*\(", r"class\s+"],
            "ruby": [r"^\s*(if|elsif|unless|while|until|for|begin|rescue)\b", r"^\s*def\s+"],
        }

        # Patterns that add to complexity
        complexity_add = {
            "python": [r"\band\b", r"\bor\b", r"\bbreak\b", r"\bcontinue\b"],
            "javascript": [r"&&", r"\|\|", r"\bbreak\b", r"\bcontinue\b"],
            "typescript": [r"&&", r"\|\|", r"\bbreak\b", r"\bcontinue\b"],
            "java": [r"&&", r"\|\|", r"\bbreak\b", r"\bcontinue\b"],
            "go": [r"&&", r"\|\|", r"\bbreak\b", r"\bcontinue\b", r"\bgoto\b"],
            "rust": [r"&&", r"\|\|", r"\bbreak\b", r"\bcontinue\b"],
            "cpp": [r"&&", r"\|\|", r"\bbreak\b", r"\bcontinue\b", r"\bgoto\b"],
            "csharp": [r"&&", r"\|\|", r"\bbreak\b", r"\bcontinue\b", r"\bgoto\b"],
            "ruby": [r"\band\b", r"\bor\b", r"\bbreak\b", r"\bnext\b"],
        }

        nest_patterns = nesting_increase.get(language, nesting_increase.get("python", []))
        add_patterns = complexity_add.get(language, complexity_add.get("python", []))

        prev_indent = 0
        for line in lines:
            if not line.strip():
                continue

            # Calculate indentation
            indent = len(line) - len(line.lstrip())

            # Track nesting based on indentation changes
            if indent > prev_indent:
                nesting_level += 1
            elif indent < prev_indent:
                nesting_level = max(0, nesting_level - 1)

            prev_indent = indent

            # Check for nesting structures (add 1 + nesting level)
            for pattern in nest_patterns:
                if re.search(pattern, line):
                    complexity += 1 + nesting_level
                    break

            # Check for complexity-adding patterns (add 1)
            for pattern in add_patterns:
                complexity += len(re.findall(pattern, line))

        return complexity

    def _calculate_max_nesting(self, code: str, language: str) -> int:
        """Calculate maximum nesting depth in code."""
        max_nesting = 0
        current_nesting = 0
        lines = code.split("\n")

        # Use indentation-based tracking
        base_indent = None

        for line in lines:
            if not line.strip():
                continue

            indent = len(line) - len(line.lstrip())

            if base_indent is None:
                base_indent = indent

            # Calculate nesting level based on indentation
            if language in ["python", "ruby"]:
                # 4 spaces or 1 tab per level
                indent_unit = 4
            else:
                # For brace languages, still use indentation as proxy
                indent_unit = 4

            relative_indent = indent - base_indent
            current_nesting = max(0, relative_indent // indent_unit)
            max_nesting = max(max_nesting, current_nesting)

        # Also count actual nesting constructs for brace languages
        if language not in ["python", "ruby"]:
            brace_nesting = 0
            max_brace_nesting = 0
            for char in code:
                if char == "{":
                    brace_nesting += 1
                    max_brace_nesting = max(max_brace_nesting, brace_nesting)
                elif char == "}":
                    brace_nesting = max(0, brace_nesting - 1)
            max_nesting = max(max_nesting, max_brace_nesting)

        return max_nesting

    def _calculate_maintainability_index(self, code: str, cyclomatic: int) -> float:
        """
        Calculate Maintainability Index (MI).

        Original formula (SEI):
        MI = 171 - 5.2 * ln(HV) - 0.23 * CC - 16.2 * ln(LOC)

        Where:
        - HV = Halstead Volume
        - CC = Cyclomatic Complexity
        - LOC = Lines of Code

        Normalized to 0-100 scale.
        """
        lines = [l for l in code.split("\n") if l.strip()]
        loc = len(lines)

        if loc == 0:
            return 100.0

        # Simplified Halstead Volume approximation
        # Count operators and operands
        operators = len(re.findall(r"[+\-*/%=<>!&|^~]|<<|>>|<=|>=|==|!=|&&|\|\|", code))
        operands = len(re.findall(r"\b\w+\b", code))

        # Avoid log(0)
        halstead_volume = max(1, (operators + operands) * math.log2(max(1, operators + operands)))

        # Calculate MI using the standard formula
        mi = 171 - 5.2 * math.log(halstead_volume) - 0.23 * cyclomatic - 16.2 * math.log(loc)

        # Normalize to 0-100
        normalized_mi = max(0, min(100, mi * 100 / 171))

        return round(normalized_mi, 2)

    def _score_cyclomatic(self, avg_complexity: float) -> float:
        """Score cyclomatic complexity (lower is better)."""
        if avg_complexity <= 5:
            return 100
        elif avg_complexity <= 10:
            return 100 - ((avg_complexity - 5) * 10)
        elif avg_complexity <= 20:
            return 50 - ((avg_complexity - 10) * 3)
        else:
            return max(0, 20 - (avg_complexity - 20))

    def _score_cognitive(self, avg_complexity: float) -> float:
        """Score cognitive complexity (lower is better)."""
        if avg_complexity <= 8:
            return 100
        elif avg_complexity <= 15:
            return 100 - ((avg_complexity - 8) * 7)
        elif avg_complexity <= 25:
            return 50 - ((avg_complexity - 15) * 3)
        else:
            return max(0, 20 - (avg_complexity - 25))

    def _score_nesting(self, avg_nesting: float) -> float:
        """Score nesting depth (lower is better)."""
        if avg_nesting <= 2:
            return 100
        elif avg_nesting <= 3:
            return 90
        elif avg_nesting <= 4:
            return 70
        elif avg_nesting <= 5:
            return 50
        else:
            return max(0, 50 - ((avg_nesting - 5) * 15))

    def _interpret_maintainability(self, mi: float) -> str:
        """Interpret maintainability index value."""
        if mi >= 80:
            return "highly_maintainable"
        elif mi >= 60:
            return "moderately_maintainable"
        elif mi >= 40:
            return "difficult_to_maintain"
        else:
            return "very_difficult_to_maintain"

    def _detect_language(self, filepath: str) -> str:
        """Detect programming language from file extension."""
        ext = "." + filepath.rsplit(".", 1)[-1].lower() if "." in filepath else ""
        for language, extensions in self.LANGUAGE_EXTENSIONS.items():
            if ext in extensions:
                return language
        return "unknown"

    # ==================== Helper Methods ====================

    def _extract_owner_repo(self, repo_url: str) -> Optional[Tuple[str, str]]:
        """Extract owner and repo from GitHub URL."""
        patterns = [
            r"github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$",
            r"github\.com:([^/]+)/([^/]+?)(?:\.git)?/?$",
        ]
        for pattern in patterns:
            match = re.search(pattern, repo_url)
            if match:
                owner = match.group(1)
                repo = match.group(2).replace(".git", "")
                return (owner, repo)
        return None

    async def _get_repo_tree(self, owner: str, repo: str) -> Optional[List[Dict]]:
        """Fetch the repository file tree from GitHub API."""
        url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/HEAD?recursive=1"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=self.github_headers)
                if response.status_code == 200:
                    data = response.json()
                    return data.get("tree", [])
                logger.warning(f"Failed to fetch tree: {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"Error fetching repo tree: {e}")
            return None

    async def _detect_primary_language(self, owner: str, repo: str) -> str:
        """Detect the primary programming language of the repository."""
        url = f"https://api.github.com/repos/{owner}/{repo}/languages"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, headers=self.github_headers)
                if response.status_code == 200:
                    languages = response.json()
                    if languages:
                        primary = max(languages, key=languages.get)
                        return primary.lower()
        except Exception as e:
            logger.debug(f"Error detecting language: {e}")

        return "python"  # Default fallback

    def _filter_code_files(
        self, tree: List[Dict], language: Optional[str] = None
    ) -> List[Dict]:
        """Filter tree to only include code files."""
        code_extensions: Set[str] = set()

        if language and language in self.LANGUAGE_EXTENSIONS:
            code_extensions.update(self.LANGUAGE_EXTENSIONS[language])
        else:
            for exts in self.LANGUAGE_EXTENSIONS.values():
                code_extensions.update(exts)

        code_files = []
        for item in tree:
            if item.get("type") != "blob":
                continue
            path = item.get("path", "")
            # Skip vendor, node_modules, etc.
            if any(skip in path for skip in ["vendor/", "node_modules/", ".git/", "__pycache__/"]):
                continue
            if any(path.endswith(ext) for ext in code_extensions):
                code_files.append(item)

        return code_files

    async def _fetch_file_contents(
        self, owner: str, repo: str, files: List[Dict]
    ) -> Dict[str, str]:
        """Fetch contents of multiple files concurrently."""
        async def fetch_one(file_info: Dict) -> Tuple[str, str]:
            path = file_info.get("path", "")
            url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"

            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(url, headers=self.github_headers)
                    if response.status_code == 200:
                        data = response.json()
                        if data.get("encoding") == "base64":
                            import base64
                            content = base64.b64decode(data.get("content", "")).decode("utf-8", errors="ignore")
                            return (path, content)
            except Exception as e:
                logger.debug(f"Error fetching {path}: {e}")

            return (path, "")

        # Limit concurrent requests
        semaphore = asyncio.Semaphore(10)

        async def fetch_with_limit(file_info: Dict) -> Tuple[str, str]:
            async with semaphore:
                return await fetch_one(file_info)

        results = await asyncio.gather(*[fetch_with_limit(f) for f in files])

        return {path: content for path, content in results if content}

    def _find_duplicate_blocks(self, file_contents: Dict[str, str]) -> List[Dict]:
        """Find duplicate code blocks across files."""
        block_hashes: Dict[str, List[Dict]] = defaultdict(list)
        duplicates = []

        for filepath, content in file_contents.items():
            lines = content.split("\n")

            # Analyze blocks of 6+ lines
            block_size = 6
            for i in range(len(lines) - block_size + 1):
                block = "\n".join(lines[i : i + block_size])
                # Normalize whitespace for comparison
                normalized = re.sub(r"\s+", " ", block.strip())
                if len(normalized) < 50:  # Skip trivial blocks
                    continue

                block_hash = hash(normalized)
                block_hashes[block_hash].append({
                    "file": filepath,
                    "start_line": i + 1,
                    "lines": block_size,
                    "preview": lines[i].strip()[:60],
                })

        # Find actual duplicates (blocks appearing in multiple places)
        for block_hash, occurrences in block_hashes.items():
            if len(occurrences) > 1:
                # Check if duplicates are in different files or far apart
                files = set(o["file"] for o in occurrences)
                if len(files) > 1 or (
                    len(occurrences) > 1
                    and abs(occurrences[0]["start_line"] - occurrences[-1]["start_line"]) > 20
                ):
                    duplicates.append({
                        "occurrences": len(occurrences),
                        "files": list(files),
                        "lines": occurrences[0]["lines"],
                        "preview": occurrences[0]["preview"],
                    })

        return sorted(duplicates, key=lambda x: x["occurrences"], reverse=True)

    def _find_similar_files(self, file_contents: Dict[str, str]) -> List[Dict]:
        """Find pairs of similar files."""
        similar_pairs = []
        filepaths = list(file_contents.keys())

        for i, path1 in enumerate(filepaths):
            for path2 in filepaths[i + 1 :]:
                similarity = self._calculate_similarity(
                    file_contents[path1], file_contents[path2]
                )
                if similarity > 0.7:  # 70% similar
                    similar_pairs.append({
                        "file1": path1,
                        "file2": path2,
                        "similarity": round(similarity, 2),
                    })

        return sorted(similar_pairs, key=lambda x: x["similarity"], reverse=True)

    def _calculate_similarity(self, content1: str, content2: str) -> float:
        """Calculate similarity ratio between two contents."""
        # Simple line-based similarity
        lines1 = set(line.strip() for line in content1.split("\n") if line.strip())
        lines2 = set(line.strip() for line in content2.split("\n") if line.strip())

        if not lines1 or not lines2:
            return 0.0

        intersection = len(lines1 & lines2)
        union = len(lines1 | lines2)

        return intersection / union if union > 0 else 0.0

    def _extract_directories(self, tree: List[Dict]) -> Set[str]:
        """Extract unique directories from file tree."""
        directories = set()
        for item in tree:
            path = item.get("path", "")
            if "/" in path:
                parts = path.split("/")
                for i in range(1, len(parts)):
                    directories.add("/".join(parts[:i]))
            if item.get("type") == "tree":
                directories.add(path)
        return directories

    def _detect_architecture_pattern(self, directories: Set[str]) -> Dict[str, Any]:
        """Detect which architectural pattern the repo follows."""
        best_match = {"pattern": "unknown", "confidence": 0, "matched_dirs": []}

        dir_names = {d.split("/")[-1].lower() for d in directories}

        for pattern_name, indicators in self.ARCHITECTURE_INDICATORS.items():
            expected_dirs = set(d.lower() for d in indicators["dirs"])
            matched = expected_dirs & dir_names
            confidence = len(matched) / len(expected_dirs) if expected_dirs else 0

            if confidence > best_match["confidence"]:
                best_match = {
                    "pattern": pattern_name,
                    "confidence": round(confidence, 2),
                    "matched_dirs": list(matched),
                }

        return best_match

    def _check_naming_consistency(self, tree: List[Dict]) -> Dict[str, Any]:
        """Check naming convention consistency."""
        files = [item for item in tree if item.get("type") == "blob"]

        snake_case = 0
        camel_case = 0
        pascal_case = 0
        kebab_case = 0

        for item in files:
            filename = item["path"].split("/")[-1].rsplit(".", 1)[0]
            if not filename:  # Skip empty filenames
                continue
            if "_" in filename and filename.islower():
                snake_case += 1
            elif "-" in filename:
                kebab_case += 1
            elif filename[0].isupper() and "_" not in filename:
                pascal_case += 1
            elif filename[0].islower() and any(c.isupper() for c in filename):
                camel_case += 1

        total = snake_case + camel_case + pascal_case + kebab_case
        if total == 0:
            return {"score": 50, "dominant_style": "unknown", "consistency": 0}

        counts = {
            "snake_case": snake_case,
            "camelCase": camel_case,
            "PascalCase": pascal_case,
            "kebab-case": kebab_case,
        }
        dominant = max(counts, key=counts.get)
        consistency = counts[dominant] / total

        return {
            "score": round(consistency * 100, 2),
            "dominant_style": dominant,
            "consistency": round(consistency, 2),
            "breakdown": counts,
        }

    def _check_module_organization(self, tree: List[Dict]) -> Dict[str, Any]:
        """Check module/package organization."""
        has_init_files = any("__init__.py" in item["path"] for item in tree)
        has_package_json = any(item["path"] == "package.json" for item in tree)
        has_setup_py = any(item["path"] == "setup.py" for item in tree)
        has_pyproject = any(item["path"] == "pyproject.toml" for item in tree)
        has_cargo = any(item["path"] == "Cargo.toml" for item in tree)
        has_go_mod = any(item["path"] == "go.mod" for item in tree)

        indicators = [
            has_init_files,
            has_package_json,
            has_setup_py or has_pyproject,
            has_cargo,
            has_go_mod,
        ]

        score = sum(40 for i in indicators if i)
        score = min(100, score)

        return {
            "score": score,
            "has_init_files": has_init_files,
            "has_package_manifest": any([has_package_json, has_setup_py, has_pyproject, has_cargo, has_go_mod]),
        }

    def _check_config_separation(self, paths: List[str]) -> bool:
        """Check if configuration is separated from code."""
        config_patterns = [
            "config/", "conf/", ".env", "settings/",
            "config.yaml", "config.json", "config.toml",
        ]
        return any(any(p in path.lower() for p in config_patterns) for path in paths)

    def _check_test_organization(self, paths: List[str]) -> bool:
        """Check if tests are properly organized."""
        test_patterns = ["test/", "tests/", "spec/", "__tests__/", "_test.go", "_test.py"]
        return any(any(p in path.lower() for p in test_patterns) for path in paths)

    def _find_long_methods(self, content: str, filepath: str) -> List[Dict]:
        """Find methods/functions that are too long."""
        long_methods = []
        lines = content.split("\n")

        # Simple heuristic: look for function definitions
        func_pattern = r"^\s*(def |function |async function |const \w+ = |public |private )"

        current_func = None
        current_start = 0
        current_indent = 0

        for i, line in enumerate(lines):
            if re.match(func_pattern, line):
                # Check previous function
                if current_func and (i - current_start) > 50:
                    long_methods.append({
                        "file": filepath,
                        "line": current_start + 1,
                        "function": current_func,
                        "length": i - current_start,
                    })
                current_func = line.strip()[:50]
                current_start = i
                current_indent = len(line) - len(line.lstrip())

        return long_methods

    def _find_deep_nesting(self, content: str, filepath: str) -> List[Dict]:
        """Find deeply nested code blocks."""
        deep_nesting = []
        lines = content.split("\n")

        for i, line in enumerate(lines):
            if not line.strip():
                continue
            indent = len(line) - len(line.lstrip())
            # Check for deep nesting (more than 4 levels, assuming 4-space indent)
            nesting_level = indent // 4
            if nesting_level > 4:
                deep_nesting.append({
                    "file": filepath,
                    "line": i + 1,
                    "nesting_level": nesting_level,
                })

        return deep_nesting

    def _prioritize_debt_issues(self, debt_indicators: Dict[str, List]) -> List[Dict]:
        """Prioritize technical debt issues by severity."""
        priority_order = [
            "hardcoded_credentials",
            "long_methods",
            "deep_nesting",
            "hack_comments",
            "deprecated_markers",
            "todo_comments",
            "commented_code",
            "magic_numbers",
        ]

        priorities = []
        for indicator in priority_order:
            if indicator in debt_indicators and debt_indicators[indicator]:
                priorities.append({
                    "indicator": indicator,
                    "count": len(debt_indicators[indicator]),
                    "severity": "high" if indicator in ["hardcoded_credentials", "long_methods"] else "medium",
                })

        return priorities[:5]

    def _calculate_overall_score(
        self,
        duplication: Dict[str, Any],
        patterns: Dict[str, Any],
        architecture: Dict[str, Any],
        debt: Dict[str, Any],
        complexity: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Calculate overall code quality score."""
        weights = {
            "duplication": 0.20,
            "design_patterns": 0.15,
            "architectural_consistency": 0.20,
            "technical_debt": 0.20,
            "complexity": 0.25,
        }

        scores = {
            "duplication": duplication.get("score", 0),
            "design_patterns": patterns.get("score", 0),
            "architectural_consistency": architecture.get("score", 0),
            "technical_debt": debt.get("score", 0),
            "complexity": complexity.get("score", 0),
        }

        weighted_sum = sum(scores[k] * weights[k] for k in weights)

        return {
            "score": round(weighted_sum, 2),
            "max_score": 100,
            "percentage": round(weighted_sum, 2),
            "status": self._get_status(weighted_sum),
            "component_scores": scores,
            "weights": weights,
        }

    def _get_status(self, score: float) -> str:
        """Get status label based on score."""
        if score >= 80:
            return "excellent"
        elif score >= 60:
            return "good"
        elif score >= 40:
            return "needs_improvement"
        else:
            return "critical"

    def _get_timestamp(self) -> str:
        """Get current timestamp in ISO format."""
        return datetime.utcnow().isoformat() + "Z"

    # ==================== Empty Result Helpers ====================

    def _empty_result(self, repo_name: str) -> Dict[str, Any]:
        """Return empty result structure when data unavailable."""
        return {
            "package_name": repo_name,
            "repository": "unknown",
            "timestamp": self._get_timestamp(),
            "duplication": self._empty_duplication(),
            "design_patterns": self._empty_patterns(),
            "architectural_consistency": self._empty_architecture(),
            "technical_debt": self._empty_debt(),
            "complexity": self._empty_complexity(),
            "overall_score": {
                "score": 0,
                "max_score": 100,
                "percentage": 0,
                "status": "unknown",
            },
        }

    def _empty_duplication(self) -> Dict[str, Any]:
        return {
            "score": 0,
            "total_files_analyzed": 0,
            "total_lines": 0,
            "duplicated_lines": 0,
            "duplication_ratio": 0,
            "duplicate_blocks": 0,
            "similar_file_pairs": 0,
            "details": {},
            "status": "unknown",
        }

    def _empty_patterns(self) -> Dict[str, Any]:
        return {
            "score": 0,
            "patterns_detected": 0,
            "total_patterns_checked": len(self.DESIGN_PATTERNS),
            "pattern_coverage": 0,
            "primary_language": "unknown",
            "patterns": {},
            "status": "unknown",
        }

    def _empty_architecture(self) -> Dict[str, Any]:
        return {
            "score": 0,
            "detected_architecture": {"pattern": "unknown", "confidence": 0},
            "naming_consistency": {"score": 0, "dominant_style": "unknown"},
            "module_organization": {"score": 0},
            "has_config_separation": False,
            "has_test_organization": False,
            "directory_count": 0,
            "status": "unknown",
        }

    def _empty_debt(self) -> Dict[str, Any]:
        return {
            "score": 0,
            "total_issues": 0,
            "total_lines_analyzed": 0,
            "issues_per_1k_lines": 0,
            "breakdown": {},
            "priority_issues": [],
            "status": "unknown",
        }

    def _empty_complexity(self) -> Dict[str, Any]:
        return {
            "score": 0,
            "cyclomatic_complexity": {
                "average": 0,
                "max": 0,
                "median": 0,
                "score": 0,
            },
            "cognitive_complexity": {
                "average": 0,
                "max": 0,
                "median": 0,
                "score": 0,
            },
            "nesting_depth": {
                "average": 0,
                "max": 0,
                "score": 0,
            },
            "maintainability_index": {
                "average": 0,
                "min": 0,
                "score": 0,
                "interpretation": "unknown",
            },
            "functions_analyzed": 0,
            "files_analyzed": 0,
            "high_complexity_functions": [],
            "deeply_nested_functions": [],
            "status": "unknown",
        }
