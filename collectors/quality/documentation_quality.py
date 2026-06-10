"""
Documentation Quality Collector

Analyzes repositories for documentation quality across multiple dimensions:
- Code comments: Inline documentation, docstrings, comment density
- API documentation: Generated docs, docstring coverage, type annotations
- Architectural documentation: Design docs, ADRs, diagrams, system overviews
- Developer onboarding: Contributing guides, setup instructions, tutorials

Metrics Covered:
- code_comments_score: Quality and coverage of inline documentation
- api_documentation_score: API reference documentation completeness
- architectural_docs_score: High-level design and architecture documentation
- onboarding_materials_score: Developer-focused getting started content
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


class DocumentationQualityCollector:
    """
    Collects documentation quality metrics by analyzing repository content,
    code comments, and documentation files.
    """

    # File patterns for different documentation types
    DOC_PATTERNS = {
        "readme": [
            r"^README\.md$",
            r"^README\.rst$",
            r"^README\.txt$",
            r"^README$",
            r"^readme\.md$",
        ],
        "contributing": [
            r"^CONTRIBUTING\.md$",
            r"^CONTRIBUTING\.rst$",
            r"^CONTRIBUTING$",
            r"^\.github/CONTRIBUTING\.md$",
            r"^docs/CONTRIBUTING\.md$",
        ],
        "changelog": [
            r"^CHANGELOG\.md$",
            r"^CHANGELOG\.rst$",
            r"^CHANGELOG$",
            r"^HISTORY\.md$",
            r"^NEWS\.md$",
            r"^CHANGES\.md$",
            r"^RELEASE_NOTES\.md$",
        ],
        "code_of_conduct": [
            r"^CODE_OF_CONDUCT\.md$",
            r"^\.github/CODE_OF_CONDUCT\.md$",
        ],
        "license": [
            r"^LICENSE$",
            r"^LICENSE\.md$",
            r"^LICENSE\.txt$",
            r"^COPYING$",
        ],
        "security": [
            r"^SECURITY\.md$",
            r"^\.github/SECURITY\.md$",
        ],
        "architecture": [
            r"^ARCHITECTURE\.md$",
            r"^DESIGN\.md$",
            r"^docs/architecture",
            r"^docs/design",
            r"^docs/adr",
            r"^adr/",
            r"^architecture/",
            r"^design/",
            r"^docs/rfcs?/",
            r"^rfcs?/",
        ],
        "api_docs": [
            r"^docs/api",
            r"^api-docs/",
            r"^reference/",
            r"^docs/reference",
            r"^apidoc/",
        ],
        "tutorials": [
            r"^tutorials?/",
            r"^docs/tutorials?",
            r"^examples?/",
            r"^docs/examples?",
            r"^guides?/",
            r"^docs/guides?",
            r"^howto/",
            r"^docs/howto",
        ],
        "getting_started": [
            r"^GETTING_STARTED\.md$",
            r"^docs/getting[_-]started",
            r"^docs/quickstart",
            r"^docs/installation",
            r"^INSTALL\.md$",
            r"^INSTALL$",
        ],
    }

    # Documentation generators and their config files
    DOC_GENERATORS = {
        "sphinx": ["conf.py", "docs/conf.py", "doc/conf.py"],
        "mkdocs": ["mkdocs.yml", "mkdocs.yaml"],
        "docusaurus": ["docusaurus.config.js", "docusaurus.config.ts"],
        "jekyll": ["_config.yml", "docs/_config.yml"],
        "hugo": ["hugo.toml", "hugo.yaml", "config.toml"],
        "vuepress": [".vuepress/config.js", "docs/.vuepress/config.js"],
        "gitbook": ["book.json", ".gitbook.yaml"],
        "doxygen": ["Doxyfile", "docs/Doxyfile", "doxygen.cfg"],
        "javadoc": ["pom.xml"],  # Will check for javadoc plugin
        "rustdoc": ["Cargo.toml"],  # Rust has built-in docs
        "godoc": ["go.mod"],  # Go has built-in docs
        "typedoc": ["typedoc.json", "typedoc.js"],
        "pdoc": ["pdoc.yml"],
        "yard": [".yardopts", "yard.yml"],
    }

    # Docstring patterns by language
    DOCSTRING_PATTERNS = {
        "python": {
            "function": r'def\s+\w+\s*\([^)]*\)\s*(?:->.*?)?:\s*(?:\n\s*)?["\']',
            "class": r'class\s+\w+.*?:\s*(?:\n\s*)?["\']',
            "module": r'^["\'][\'"]{2}',
        },
        "javascript": {
            "jsdoc": r'/\*\*[\s\S]*?\*/',
            "function": r'/\*\*[\s\S]*?\*/\s*(?:export\s+)?(?:async\s+)?function',
        },
        "typescript": {
            "tsdoc": r'/\*\*[\s\S]*?\*/',
            "interface": r'/\*\*[\s\S]*?\*/\s*(?:export\s+)?interface',
        },
        "java": {
            "javadoc": r'/\*\*[\s\S]*?\*/',
            "class": r'/\*\*[\s\S]*?\*/\s*(?:public|private|protected)?\s*class',
        },
        "rust": {
            "doc_comment": r'///.*|//!.*|/\*\*[\s\S]*?\*/',
        },
        "go": {
            "doc_comment": r'//\s*\w+.*',  # Go doc comments start with identifier
        },
        "cpp": {
            "doxygen": r'/\*\*[\s\S]*?\*/|///.*|//!.*',
        },
    }

    # Comment quality indicators
    COMMENT_QUALITY_INDICATORS = {
        "good": [
            r"TODO\s*\([^)]+\):",  # TODO with owner
            r"FIXME\s*\([^)]+\):",  # FIXME with owner
            r"NOTE:",  # Explanatory notes
            r"IMPORTANT:",  # Important callouts
            r"WARNING:",  # Warnings
            r"SAFETY:",  # Safety notes (Rust)
            r"INVARIANT:",  # Invariant documentation
            r"PRECONDITION:",  # Pre/postconditions
            r"POSTCONDITION:",
            r"@param\s+\w+",  # Parameter documentation
            r"@returns?",  # Return documentation
            r"@throws?",  # Exception documentation
            r"@example",  # Examples in docs
            r"Args:",  # Python docstring sections
            r"Returns:",
            r"Raises:",
            r"Example:",
            r"See Also:",
        ],
        "bad": [
            r"//\s*TODO\s*$",  # Empty TODO
            r"//\s*FIXME\s*$",  # Empty FIXME
            r"//\s*HACK\s*$",  # Empty HACK
            r"//\s*XXX\s*$",  # Empty XXX
            r"#\s*TODO\s*$",
            r"#\s*FIXME\s*$",
            r"//\s*\w+\s*$",  # Single word comments
            r"#\s*\w+\s*$",
            r"//\s*end\s",  # Redundant end comments
            r"//\s*close\s",
            r"{\s*//\s*$",  # Empty block comments
        ],
    }

    # Code file extensions
    CODE_EXTENSIONS = {
        "python": [".py"],
        "javascript": [".js", ".jsx", ".mjs"],
        "typescript": [".ts", ".tsx"],
        "java": [".java"],
        "rust": [".rs"],
        "go": [".go"],
        "cpp": [".cpp", ".cc", ".cxx", ".hpp", ".h"],
        "csharp": [".cs"],
        "ruby": [".rb"],
        "php": [".php"],
        "swift": [".swift"],
        "kotlin": [".kt", ".kts"],
        "scala": [".scala"],
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

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        """
        Main entry point - collects all documentation quality metrics.

        Args:
            package: Dictionary containing package info with 'repo_url' key

        Returns:
            Dictionary with all collected metrics and scores
        """
        repo_url = package.get("repo_url", "")
        repo_name = package.get("name", "unknown")

        logger.info(f"Collecting documentation quality metrics for {repo_name}")

        owner_repo = self._extract_owner_repo(repo_url)
        if not owner_repo:
            logger.warning(f"Could not extract owner/repo from URL: {repo_url}")
            return self._empty_result(repo_name)

        owner, repo = owner_repo

        try:
            # Get repository tree first
            tree = await self._get_repo_tree(owner, repo)
            if not tree:
                return self._empty_result(repo_name)

            # Collect all metrics concurrently
            results = await asyncio.gather(
                self._analyze_code_comments(owner, repo, tree),
                self._analyze_api_documentation(owner, repo, tree),
                self._analyze_architectural_docs(owner, repo, tree),
                self._analyze_onboarding_materials(owner, repo, tree),
                return_exceptions=True,
            )

            # Handle exceptions
            code_comments = results[0] if not isinstance(results[0], Exception) else self._empty_code_comments()
            api_docs = results[1] if not isinstance(results[1], Exception) else self._empty_api_docs()
            arch_docs = results[2] if not isinstance(results[2], Exception) else self._empty_arch_docs()
            onboarding = results[3] if not isinstance(results[3], Exception) else self._empty_onboarding()

            # Log exceptions
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"Error in documentation analysis {i}: {result}")

            # Calculate overall score
            overall_score = self._calculate_overall_score(
                code_comments, api_docs, arch_docs, onboarding
            )

            return {
                "package_name": repo_name,
                "repository": f"{owner}/{repo}",
                "timestamp": self._get_timestamp(),
                "code_comments": code_comments,
                "api_documentation": api_docs,
                "architectural_docs": arch_docs,
                "onboarding_materials": onboarding,
                "overall_score": overall_score,
            }

        except Exception as e:
            logger.error(f"Error collecting documentation metrics for {repo_name}: {e}")
            return self._empty_result(repo_name)

    # ==================== Code Comments Analysis ====================

    async def _analyze_code_comments(
        self, owner: str, repo: str, tree: List[Dict]
    ) -> Dict[str, Any]:
        """
        Analyze code comment quality and coverage.

        Evaluates:
        - Comment density (comments per line of code)
        - Docstring coverage for functions/classes
        - Comment quality indicators
        - Inline documentation patterns
        """
        logger.debug(f"Analyzing code comments for {owner}/{repo}")

        try:
            # Filter to code files
            code_files = self._filter_code_files(tree)
            sample_files = code_files[:40]

            # Fetch contents
            file_contents = await self._fetch_file_contents(owner, repo, sample_files)

            if not file_contents:
                return self._empty_code_comments()

            # Analyze each file
            total_lines = 0
            total_comment_lines = 0
            total_functions = 0
            documented_functions = 0
            total_classes = 0
            documented_classes = 0
            quality_indicators = {"good": 0, "bad": 0}
            language_stats: Dict[str, Dict] = defaultdict(lambda: {
                "files": 0, "lines": 0, "comments": 0, "docstrings": 0
            })

            for filepath, content in file_contents.items():
                language = self._detect_language(filepath)
                lines = content.split("\n")
                total_lines += len(lines)
                language_stats[language]["files"] += 1
                language_stats[language]["lines"] += len(lines)

                # Count comment lines
                comment_lines = self._count_comment_lines(content, language)
                total_comment_lines += comment_lines
                language_stats[language]["comments"] += comment_lines

                # Check docstring coverage
                func_count, doc_func_count = self._count_documented_functions(content, language)
                total_functions += func_count
                documented_functions += doc_func_count

                class_count, doc_class_count = self._count_documented_classes(content, language)
                total_classes += class_count
                documented_classes += doc_class_count

                language_stats[language]["docstrings"] += doc_func_count + doc_class_count

                # Check comment quality
                good_count = self._count_quality_indicators(content, "good")
                bad_count = self._count_quality_indicators(content, "bad")
                quality_indicators["good"] += good_count
                quality_indicators["bad"] += bad_count

            # Calculate metrics
            comment_density = total_comment_lines / max(total_lines, 1)
            function_doc_coverage = documented_functions / max(total_functions, 1)
            class_doc_coverage = documented_classes / max(total_classes, 1)

            # Quality ratio (good vs bad indicators)
            total_indicators = quality_indicators["good"] + quality_indicators["bad"]
            quality_ratio = quality_indicators["good"] / max(total_indicators, 1)

            # Calculate score
            # Optimal comment density is ~15-25%
            density_score = self._score_comment_density(comment_density)
            coverage_score = ((function_doc_coverage + class_doc_coverage) / 2) * 100
            quality_score = quality_ratio * 100

            # Weighted final score
            score = (density_score * 0.3) + (coverage_score * 0.5) + (quality_score * 0.2)

            return {
                "score": round(score, 2),
                "total_lines_analyzed": total_lines,
                "total_comment_lines": total_comment_lines,
                "comment_density": round(comment_density, 4),
                "comment_density_percentage": round(comment_density * 100, 2),
                "docstring_coverage": {
                    "functions": {
                        "total": total_functions,
                        "documented": documented_functions,
                        "percentage": round(function_doc_coverage * 100, 2),
                    },
                    "classes": {
                        "total": total_classes,
                        "documented": documented_classes,
                        "percentage": round(class_doc_coverage * 100, 2),
                    },
                },
                "quality_indicators": quality_indicators,
                "quality_ratio": round(quality_ratio, 2),
                "by_language": dict(language_stats),
                "files_analyzed": len(file_contents),
                "status": self._get_status(score),
            }

        except Exception as e:
            logger.error(f"Error analyzing code comments: {e}")
            return self._empty_code_comments()

    def _count_comment_lines(self, content: str, language: str) -> int:
        """Count lines that are comments."""
        lines = content.split("\n")
        comment_lines = 0
        in_block_comment = False

        for line in lines:
            stripped = line.strip()

            # Handle block comments
            if language in ["python"]:
                if '"""' in stripped or "'''" in stripped:
                    # Toggle or count docstring
                    quotes = stripped.count('"""') + stripped.count("'''")
                    if quotes == 1:
                        in_block_comment = not in_block_comment
                    comment_lines += 1
                    continue
                if in_block_comment:
                    comment_lines += 1
                    continue
                if stripped.startswith("#"):
                    comment_lines += 1

            elif language in ["javascript", "typescript", "java", "cpp", "csharp", "go", "rust", "swift", "kotlin", "scala"]:
                if "/*" in stripped and "*/" in stripped:
                    comment_lines += 1
                    continue
                if "/*" in stripped:
                    in_block_comment = True
                    comment_lines += 1
                    continue
                if "*/" in stripped:
                    in_block_comment = False
                    comment_lines += 1
                    continue
                if in_block_comment:
                    comment_lines += 1
                    continue
                if stripped.startswith("//"):
                    comment_lines += 1

            elif language == "ruby":
                if stripped.startswith("=begin"):
                    in_block_comment = True
                elif stripped.startswith("=end"):
                    in_block_comment = False
                elif in_block_comment or stripped.startswith("#"):
                    comment_lines += 1

        return comment_lines

    def _count_documented_functions(self, content: str, language: str) -> Tuple[int, int]:
        """Count total functions and documented functions."""
        total = 0
        documented = 0

        if language == "python":
            # Match function definitions
            func_pattern = r'def\s+(\w+)\s*\([^)]*\)'
            functions = re.finditer(func_pattern, content)

            for match in functions:
                total += 1
                # Check if followed by docstring
                after = content[match.end():]
                if re.match(r'\s*(?:->.*?)?:\s*(?:\n\s*)?["\']', after):
                    documented += 1

        elif language in ["javascript", "typescript"]:
            # Match function definitions
            func_pattern = r'(?:export\s+)?(?:async\s+)?function\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\('
            functions = re.finditer(func_pattern, content)

            for match in functions:
                total += 1
                # Check for JSDoc before function
                before = content[:match.start()]
                if re.search(r'/\*\*[\s\S]*?\*/\s*$', before):
                    documented += 1

        elif language == "java":
            func_pattern = r'(?:public|private|protected)\s+(?:static\s+)?(?:\w+(?:<[^>]+>)?)\s+(\w+)\s*\('
            functions = re.finditer(func_pattern, content)

            for match in functions:
                total += 1
                before = content[:match.start()]
                if re.search(r'/\*\*[\s\S]*?\*/\s*$', before):
                    documented += 1

        elif language == "go":
            func_pattern = r'func\s+(?:\([^)]+\)\s+)?(\w+)\s*\('
            functions = re.finditer(func_pattern, content)

            for match in functions:
                total += 1
                before = content[:match.start()]
                # Go doc comments are // comments directly before
                if re.search(r'//\s*\w+[^\n]*\n$', before):
                    documented += 1

        elif language == "rust":
            func_pattern = r'(?:pub\s+)?fn\s+(\w+)'
            functions = re.finditer(func_pattern, content)

            for match in functions:
                total += 1
                before = content[:match.start()]
                if re.search(r'///[^\n]*\n$|/\*\*[\s\S]*?\*/\s*$', before):
                    documented += 1

        return total, documented

    def _count_documented_classes(self, content: str, language: str) -> Tuple[int, int]:
        """Count total classes and documented classes."""
        total = 0
        documented = 0

        if language == "python":
            class_pattern = r'class\s+(\w+)'
            classes = re.finditer(class_pattern, content)

            for match in classes:
                total += 1
                after = content[match.end():]
                if re.match(r'[^:]*:\s*(?:\n\s*)?["\']', after):
                    documented += 1

        elif language in ["javascript", "typescript"]:
            class_pattern = r'(?:export\s+)?class\s+(\w+)'
            classes = re.finditer(class_pattern, content)

            for match in classes:
                total += 1
                before = content[:match.start()]
                if re.search(r'/\*\*[\s\S]*?\*/\s*$', before):
                    documented += 1

        elif language == "java":
            class_pattern = r'(?:public|private|protected)?\s*class\s+(\w+)'
            classes = re.finditer(class_pattern, content)

            for match in classes:
                total += 1
                before = content[:match.start()]
                if re.search(r'/\*\*[\s\S]*?\*/\s*$', before):
                    documented += 1

        return total, documented

    def _count_quality_indicators(self, content: str, quality_type: str) -> int:
        """Count quality indicators in content."""
        count = 0
        patterns = self.COMMENT_QUALITY_INDICATORS.get(quality_type, [])
        for pattern in patterns:
            count += len(re.findall(pattern, content, re.IGNORECASE))
        return count

    def _score_comment_density(self, density: float) -> float:
        """Score comment density - optimal is 15-25%."""
        optimal_min = 0.15
        optimal_max = 0.25

        if optimal_min <= density <= optimal_max:
            return 100
        elif density < optimal_min:
            # Too few comments
            return (density / optimal_min) * 100
        else:
            # Too many comments (might indicate commented-out code)
            excess = density - optimal_max
            return max(0, 100 - (excess * 200))

    # ==================== API Documentation Analysis ====================

    async def _analyze_api_documentation(
        self, owner: str, repo: str, tree: List[Dict]
    ) -> Dict[str, Any]:
        """
        Analyze API documentation quality.

        Checks for:
        - Documentation generator configuration
        - Generated documentation directories
        - Type annotations/hints
        - Public API documentation
        """
        logger.debug(f"Analyzing API documentation for {owner}/{repo}")

        try:
            all_paths = [item["path"] for item in tree]

            # Check for documentation generators
            detected_generators = []
            for generator, config_files in self.DOC_GENERATORS.items():
                for config in config_files:
                    if any(path == config or path.endswith(f"/{config}") for path in all_paths):
                        detected_generators.append(generator)
                        break

            # Check for API documentation directories
            api_doc_dirs = []
            for pattern in self.DOC_PATTERNS["api_docs"]:
                for path in all_paths:
                    if re.search(pattern, path, re.IGNORECASE):
                        dir_path = path.rsplit("/", 1)[0] if "/" in path else path
                        if dir_path not in api_doc_dirs:
                            api_doc_dirs.append(dir_path)

            # Count documentation files
            doc_file_count = sum(
                1 for path in all_paths
                if path.endswith((".md", ".rst", ".txt", ".html"))
                and any(d in path for d in ["docs/", "doc/", "documentation/"])
            )

            # Check for type annotations in code
            code_files = self._filter_code_files(tree)
            sample_files = code_files[:20]
            file_contents = await self._fetch_file_contents(owner, repo, sample_files)

            type_annotation_stats = self._analyze_type_annotations(file_contents)

            # Check for README API section
            readme_has_api = await self._check_readme_api_section(owner, repo, tree)

            # Check for hosted documentation links
            hosted_docs = await self._check_hosted_docs(owner, repo)

            # Calculate score
            generator_score = min(30, len(detected_generators) * 15)
            api_dirs_score = min(20, len(api_doc_dirs) * 10)
            doc_files_score = min(20, doc_file_count * 2)
            type_score = type_annotation_stats.get("coverage", 0) * 0.2
            readme_score = 10 if readme_has_api else 0
            hosted_score = 10 if hosted_docs.get("has_hosted_docs") else 0

            score = generator_score + api_dirs_score + doc_files_score + type_score + readme_score + hosted_score

            return {
                "score": round(min(100, score), 2),
                "documentation_generators": detected_generators,
                "api_doc_directories": api_doc_dirs,
                "doc_file_count": doc_file_count,
                "type_annotations": type_annotation_stats,
                "readme_has_api_section": readme_has_api,
                "hosted_documentation": hosted_docs,
                "status": self._get_status(score),
            }

        except Exception as e:
            logger.error(f"Error analyzing API documentation: {e}")
            return self._empty_api_docs()

    def _analyze_type_annotations(self, file_contents: Dict[str, str]) -> Dict[str, Any]:
        """Analyze type annotation coverage in code files."""
        total_functions = 0
        typed_functions = 0
        languages_with_types = []

        for filepath, content in file_contents.items():
            language = self._detect_language(filepath)

            if language == "python":
                # Check for type hints
                func_pattern = r'def\s+\w+\s*\(([^)]*)\)'
                typed_pattern = r'def\s+\w+\s*\([^)]*\)\s*->'

                total_functions += len(re.findall(func_pattern, content))
                typed_functions += len(re.findall(typed_pattern, content))

                if "from typing import" in content or ": " in content:
                    if "python" not in languages_with_types:
                        languages_with_types.append("python")

            elif language == "typescript":
                # TypeScript has types by design
                if "typescript" not in languages_with_types:
                    languages_with_types.append("typescript")
                # Count interface/type definitions as "typed"
                typed_functions += len(re.findall(r'interface\s+\w+|type\s+\w+\s*=', content))
                total_functions += typed_functions

            elif language == "rust":
                # Rust requires types
                if "rust" not in languages_with_types:
                    languages_with_types.append("rust")

            elif language == "go":
                # Go requires types
                if "go" not in languages_with_types:
                    languages_with_types.append("go")

        coverage = typed_functions / max(total_functions, 1) * 100

        return {
            "total_functions_checked": total_functions,
            "typed_functions": typed_functions,
            "coverage": round(coverage, 2),
            "languages_with_types": languages_with_types,
        }

    async def _check_readme_api_section(
        self, owner: str, repo: str, tree: List[Dict]
    ) -> bool:
        """Check if README has an API documentation section."""
        # Find README
        readme_path = None
        for item in tree:
            if re.match(r"^README\.(md|rst|txt)?$", item["path"], re.IGNORECASE):
                readme_path = item["path"]
                break

        if not readme_path:
            return False

        try:
            content = await self._fetch_single_file(owner, repo, readme_path)
            if content:
                # Look for API section headers
                api_patterns = [
                    r"^#+\s*API",
                    r"^#+\s*Reference",
                    r"^#+\s*Documentation",
                    r"^#+\s*Usage",
                    r"^#+\s*Methods",
                    r"^#+\s*Functions",
                ]
                for pattern in api_patterns:
                    if re.search(pattern, content, re.MULTILINE | re.IGNORECASE):
                        return True
        except Exception:
            pass

        return False

    async def _check_hosted_docs(self, owner: str, repo: str) -> Dict[str, Any]:
        """Check for hosted documentation (ReadTheDocs, GitHub Pages, etc.)."""
        hosted_docs = {
            "has_hosted_docs": False,
            "platforms": [],
            "urls": [],
        }

        try:
            # Check for .readthedocs.yml
            async with httpx.AsyncClient(timeout=10.0) as client:
                rtd_url = f"https://api.github.com/repos/{owner}/{repo}/contents/.readthedocs.yml"
                headers = {**self.github_headers}
                response = await client.get(rtd_url, headers=headers)
                if response.status_code == 200:
                    hosted_docs["has_hosted_docs"] = True
                    hosted_docs["platforms"].append("readthedocs")
                    hosted_docs["urls"].append(f"https://{repo}.readthedocs.io")

                # Check repo description/homepage for doc links
                repo_url = f"https://api.github.com/repos/{owner}/{repo}"
                response = await client.get(repo_url, headers=headers)
                if response.status_code == 200:
                    data = response.json()
                    homepage = data.get("homepage", "")
                    if homepage:
                        if "github.io" in homepage:
                            hosted_docs["has_hosted_docs"] = True
                            hosted_docs["platforms"].append("github_pages")
                            hosted_docs["urls"].append(homepage)
                        elif "readthedocs" in homepage:
                            if "readthedocs" not in hosted_docs["platforms"]:
                                hosted_docs["has_hosted_docs"] = True
                                hosted_docs["platforms"].append("readthedocs")
                                hosted_docs["urls"].append(homepage)
                        elif homepage:
                            # Generic documentation site
                            hosted_docs["has_hosted_docs"] = True
                            hosted_docs["platforms"].append("custom")
                            hosted_docs["urls"].append(homepage)

        except Exception as e:
            logger.debug(f"Error checking hosted docs: {e}")

        return hosted_docs

    # ==================== Architectural Documentation Analysis ====================

    async def _analyze_architectural_docs(
        self, owner: str, repo: str, tree: List[Dict]
    ) -> Dict[str, Any]:
        """
        Analyze architectural documentation.

        Checks for:
        - Architecture decision records (ADRs)
        - Design documents
        - System diagrams
        - High-level overviews
        - RFCs
        """
        logger.debug(f"Analyzing architectural docs for {owner}/{repo}")

        try:
            all_paths = [item["path"] for item in tree]

            # Check for architecture-related directories and files
            arch_files = []
            arch_dirs = set()

            for pattern in self.DOC_PATTERNS["architecture"]:
                for path in all_paths:
                    if re.search(pattern, path, re.IGNORECASE):
                        arch_files.append(path)
                        if "/" in path:
                            arch_dirs.add(path.rsplit("/", 1)[0])

            # Check for diagram files
            diagram_extensions = [".svg", ".png", ".jpg", ".jpeg", ".gif", ".drawio", ".mmd", ".puml"]
            diagram_patterns = ["diagram", "architecture", "design", "flow", "sequence", "class", "component"]

            diagrams = []
            for path in all_paths:
                ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
                if ext in diagram_extensions:
                    # Check if in docs directory or has architecture-related name
                    if any(p in path.lower() for p in ["docs/", "doc/", "images/", "assets/"]):
                        if any(dp in path.lower() for dp in diagram_patterns):
                            diagrams.append(path)

            # Check for ADRs specifically
            adr_files = [p for p in all_paths if re.search(r"adr[-_/]?\d+|ADR[-_]?\d+", p)]

            # Check for RFCs
            rfc_files = [p for p in all_paths if re.search(r"rfc[-_/]?\d+|RFC[-_]?\d+", p)]

            # Check README for architecture section
            has_arch_in_readme = await self._check_readme_section(
                owner, repo, tree,
                ["architecture", "design", "structure", "overview", "how it works"]
            )

            # Check for specific architecture documents
            specific_docs = {
                "architecture_md": any("ARCHITECTURE" in p.upper() for p in all_paths),
                "design_md": any("DESIGN" in p.upper() for p in all_paths),
                "contributing_has_arch": False,  # Will check below
            }

            # Check CONTRIBUTING for architecture guidance
            contributing_content = await self._get_contributing_content(owner, repo, tree)
            if contributing_content:
                if re.search(r"architecture|design|structure", contributing_content, re.IGNORECASE):
                    specific_docs["contributing_has_arch"] = True

            # Calculate score
            score = 0
            score += min(25, len(arch_files) * 5)  # Architecture files
            score += min(15, len(diagrams) * 5)  # Diagrams
            score += min(20, len(adr_files) * 4)  # ADRs
            score += min(10, len(rfc_files) * 5)  # RFCs
            score += 10 if has_arch_in_readme else 0
            score += 10 if specific_docs["architecture_md"] else 0
            score += 5 if specific_docs["design_md"] else 0
            score += 5 if specific_docs["contributing_has_arch"] else 0

            return {
                "score": round(min(100, score), 2),
                "architecture_files": arch_files[:10],  # Limit output
                "architecture_directories": list(arch_dirs),
                "diagrams": diagrams[:10],
                "adr_count": len(adr_files),
                "adr_files": adr_files[:5],
                "rfc_count": len(rfc_files),
                "rfc_files": rfc_files[:5],
                "has_architecture_in_readme": has_arch_in_readme,
                "specific_documents": specific_docs,
                "status": self._get_status(score),
            }

        except Exception as e:
            logger.error(f"Error analyzing architectural docs: {e}")
            return self._empty_arch_docs()

    async def _check_readme_section(
        self, owner: str, repo: str, tree: List[Dict], keywords: List[str]
    ) -> bool:
        """Check if README contains sections with given keywords."""
        readme_path = None
        for item in tree:
            if re.match(r"^README\.(md|rst|txt)?$", item["path"], re.IGNORECASE):
                readme_path = item["path"]
                break

        if not readme_path:
            return False

        try:
            content = await self._fetch_single_file(owner, repo, readme_path)
            if content:
                for keyword in keywords:
                    pattern = rf"^#+\s*.*{keyword}"
                    if re.search(pattern, content, re.MULTILINE | re.IGNORECASE):
                        return True
        except Exception:
            pass

        return False

    async def _get_contributing_content(
        self, owner: str, repo: str, tree: List[Dict]
    ) -> Optional[str]:
        """Get CONTRIBUTING file content."""
        for pattern in self.DOC_PATTERNS["contributing"]:
            for item in tree:
                if re.match(pattern, item["path"], re.IGNORECASE):
                    return await self._fetch_single_file(owner, repo, item["path"])
        return None

    # ==================== Onboarding Materials Analysis ====================

    async def _analyze_onboarding_materials(
        self, owner: str, repo: str, tree: List[Dict]
    ) -> Dict[str, Any]:
        """
        Analyze developer onboarding materials.

        Checks for:
        - CONTRIBUTING guide
        - Getting started documentation
        - Development setup instructions
        - Examples and tutorials
        - Issue/PR templates
        """
        logger.debug(f"Analyzing onboarding materials for {owner}/{repo}")

        try:
            all_paths = [item["path"] for item in tree]

            # Check for standard onboarding files
            onboarding_files = {
                "readme": False,
                "contributing": False,
                "code_of_conduct": False,
                "changelog": False,
                "license": False,
                "security": False,
                "getting_started": False,
            }

            for doc_type, patterns in self.DOC_PATTERNS.items():
                if doc_type in onboarding_files:
                    for pattern in patterns:
                        if any(re.match(pattern, p, re.IGNORECASE) for p in all_paths):
                            onboarding_files[doc_type] = True
                            break

            # Check for examples/tutorials
            examples = []
            tutorials = []
            for pattern in self.DOC_PATTERNS["tutorials"]:
                for path in all_paths:
                    if re.search(pattern, path, re.IGNORECASE):
                        if "example" in path.lower():
                            examples.append(path)
                        else:
                            tutorials.append(path)

            # Check for issue/PR templates
            templates = {
                "issue_template": any(".github/ISSUE_TEMPLATE" in p or "ISSUE_TEMPLATE" in p for p in all_paths),
                "pr_template": any("PULL_REQUEST_TEMPLATE" in p for p in all_paths),
                "bug_report": any("bug_report" in p.lower() for p in all_paths),
                "feature_request": any("feature_request" in p.lower() for p in all_paths),
            }

            # Analyze README quality for onboarding
            readme_quality = await self._analyze_readme_onboarding(owner, repo, tree)

            # Check for development setup documentation
            dev_setup = {
                "makefile": any(p.lower() == "makefile" for p in all_paths),
                "docker_compose": any("docker-compose" in p.lower() for p in all_paths),
                "devcontainer": any(".devcontainer" in p for p in all_paths),
                "setup_script": any(re.match(r"setup\.(sh|py|js)$", p.lower()) for p in all_paths),
                "requirements": any(p in ["requirements.txt", "requirements-dev.txt", "pyproject.toml"] for p in all_paths),
                "package_json": any(p == "package.json" for p in all_paths),
            }

            # Calculate score
            score = 0

            # Essential files (40 points max)
            score += 15 if onboarding_files["readme"] else 0
            score += 10 if onboarding_files["contributing"] else 0
            score += 5 if onboarding_files["license"] else 0
            score += 5 if onboarding_files["changelog"] else 0
            score += 5 if onboarding_files["code_of_conduct"] else 0

            # Examples and tutorials (20 points max)
            score += min(10, len(examples) * 2)
            score += min(10, len(tutorials) * 3)

            # Templates (10 points max)
            score += 3 if templates["issue_template"] else 0
            score += 3 if templates["pr_template"] else 0
            score += 2 if templates["bug_report"] else 0
            score += 2 if templates["feature_request"] else 0

            # Dev setup (15 points max)
            score += sum(3 for v in dev_setup.values() if v)

            # README quality (15 points max)
            score += readme_quality.get("score", 0) * 0.15

            return {
                "score": round(min(100, score), 2),
                "essential_files": onboarding_files,
                "essential_files_count": sum(onboarding_files.values()),
                "examples": {
                    "count": len(examples),
                    "paths": examples[:5],
                },
                "tutorials": {
                    "count": len(tutorials),
                    "paths": tutorials[:5],
                },
                "templates": templates,
                "dev_setup": dev_setup,
                "readme_quality": readme_quality,
                "status": self._get_status(score),
            }

        except Exception as e:
            logger.error(f"Error analyzing onboarding materials: {e}")
            return self._empty_onboarding()

    async def _analyze_readme_onboarding(
        self, owner: str, repo: str, tree: List[Dict]
    ) -> Dict[str, Any]:
        """Analyze README for onboarding-friendly content."""
        readme_path = None
        for item in tree:
            if re.match(r"^README\.(md|rst|txt)?$", item["path"], re.IGNORECASE):
                readme_path = item["path"]
                break

        if not readme_path:
            return {"score": 0, "sections": {}, "has_badges": False}

        try:
            content = await self._fetch_single_file(owner, repo, readme_path)
            if not content:
                return {"score": 0, "sections": {}, "has_badges": False}

            # Check for important sections
            sections = {
                "installation": bool(re.search(r"^#+\s*(install|setup|getting started)", content, re.MULTILINE | re.IGNORECASE)),
                "usage": bool(re.search(r"^#+\s*(usage|how to use|quick start)", content, re.MULTILINE | re.IGNORECASE)),
                "examples": bool(re.search(r"^#+\s*example", content, re.MULTILINE | re.IGNORECASE)),
                "api": bool(re.search(r"^#+\s*(api|reference|documentation)", content, re.MULTILINE | re.IGNORECASE)),
                "contributing": bool(re.search(r"^#+\s*contribut", content, re.MULTILINE | re.IGNORECASE)),
                "license": bool(re.search(r"^#+\s*license", content, re.MULTILINE | re.IGNORECASE)),
            }

            # Check for code blocks (indicates examples)
            code_blocks = len(re.findall(r"```", content))

            # Check for badges
            has_badges = bool(re.search(r"\[!\[.*?\]\(.*?\)\]|\!\[.*?\]\(https://.*?badge", content))

            # Calculate README quality score
            score = sum(15 for v in sections.values() if v)
            score += min(20, code_blocks * 2)
            score += 10 if has_badges else 0

            return {
                "score": min(100, score),
                "sections": sections,
                "code_blocks": code_blocks,
                "has_badges": has_badges,
                "length": len(content),
            }

        except Exception:
            return {"score": 0, "sections": {}, "has_badges": False}

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

    def _filter_code_files(self, tree: List[Dict]) -> List[Dict]:
        """Filter tree to only include code files."""
        all_extensions: Set[str] = set()
        for exts in self.CODE_EXTENSIONS.values():
            all_extensions.update(exts)

        code_files = []
        for item in tree:
            if item.get("type") != "blob":
                continue
            path = item.get("path", "")
            # Skip vendor, node_modules, etc.
            if any(skip in path for skip in ["vendor/", "node_modules/", ".git/", "__pycache__/", "dist/", "build/"]):
                continue
            if any(path.endswith(ext) for ext in all_extensions):
                code_files.append(item)

        return code_files

    def _detect_language(self, filepath: str) -> str:
        """Detect programming language from file extension."""
        ext = "." + filepath.rsplit(".", 1)[-1].lower() if "." in filepath else ""
        for language, extensions in self.CODE_EXTENSIONS.items():
            if ext in extensions:
                return language
        return "unknown"

    async def _fetch_file_contents(
        self, owner: str, repo: str, files: List[Dict]
    ) -> Dict[str, str]:
        """Fetch contents of multiple files concurrently."""
        async def fetch_one(file_info: Dict) -> Tuple[str, str]:
            path = file_info.get("path", "")
            content = await self._fetch_single_file(owner, repo, path)
            return (path, content or "")

        semaphore = asyncio.Semaphore(10)

        async def fetch_with_limit(file_info: Dict) -> Tuple[str, str]:
            async with semaphore:
                return await fetch_one(file_info)

        results = await asyncio.gather(*[fetch_with_limit(f) for f in files])
        return {path: content for path, content in results if content}

    async def _fetch_single_file(self, owner: str, repo: str, path: str) -> Optional[str]:
        """Fetch a single file's content."""
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, headers=self.github_headers)
                if response.status_code == 200:
                    data = response.json()
                    if data.get("encoding") == "base64":
                        import base64
                        return base64.b64decode(data.get("content", "")).decode("utf-8", errors="ignore")
        except Exception as e:
            logger.debug(f"Error fetching {path}: {e}")

        return None

    def _calculate_overall_score(
        self,
        code_comments: Dict[str, Any],
        api_docs: Dict[str, Any],
        arch_docs: Dict[str, Any],
        onboarding: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Calculate overall documentation quality score."""
        weights = {
            "code_comments": 0.25,
            "api_documentation": 0.30,
            "architectural_docs": 0.20,
            "onboarding_materials": 0.25,
        }

        scores = {
            "code_comments": code_comments.get("score", 0),
            "api_documentation": api_docs.get("score", 0),
            "architectural_docs": arch_docs.get("score", 0),
            "onboarding_materials": onboarding.get("score", 0),
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
            return "poor"

    def _get_timestamp(self) -> str:
        """Get current timestamp in ISO format."""
        return datetime.utcnow().isoformat() + "Z"

    # ==================== Empty Result Helpers ====================

    def _empty_result(self, repo_name: str) -> Dict[str, Any]:
        """Return empty result structure."""
        return {
            "package_name": repo_name,
            "repository": "unknown",
            "timestamp": self._get_timestamp(),
            "code_comments": self._empty_code_comments(),
            "api_documentation": self._empty_api_docs(),
            "architectural_docs": self._empty_arch_docs(),
            "onboarding_materials": self._empty_onboarding(),
            "overall_score": {
                "score": 0,
                "max_score": 100,
                "percentage": 0,
                "status": "unknown",
            },
        }

    def _empty_code_comments(self) -> Dict[str, Any]:
        return {
            "score": 0,
            "total_lines_analyzed": 0,
            "comment_density": 0,
            "docstring_coverage": {"functions": {}, "classes": {}},
            "quality_indicators": {"good": 0, "bad": 0},
            "status": "unknown",
        }

    def _empty_api_docs(self) -> Dict[str, Any]:
        return {
            "score": 0,
            "documentation_generators": [],
            "api_doc_directories": [],
            "doc_file_count": 0,
            "type_annotations": {},
            "hosted_documentation": {},
            "status": "unknown",
        }

    def _empty_arch_docs(self) -> Dict[str, Any]:
        return {
            "score": 0,
            "architecture_files": [],
            "diagrams": [],
            "adr_count": 0,
            "rfc_count": 0,
            "status": "unknown",
        }

    def _empty_onboarding(self) -> Dict[str, Any]:
        return {
            "score": 0,
            "essential_files": {},
            "examples": {"count": 0, "paths": []},
            "tutorials": {"count": 0, "paths": []},
            "templates": {},
            "dev_setup": {},
            "status": "unknown",
        }
