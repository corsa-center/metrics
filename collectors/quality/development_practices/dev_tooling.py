"""
Development Tooling Collector (CASS Report Section 4.3.2 — Development Practices)

Fills the three sub-metrics that CICDMetricsCollector does not cover:

  - Testing Framework Excellence  : test directories and framework configuration
  - Code Review Quality Analysis  : share of merged PRs that were actually reviewed
  - Development Tool Integration  : linters, formatters, pre-commit, dependency bots

CI/CD Effectiveness Assessment and Community Contribution Facilitation are
handled elsewhere (ci_cd.py and the OpenSSF badge respectively).
"""

import asyncio
import base64
import logging
import re
from typing import Any, Dict, List

import httpx

from collectors.ecosystem.base import (
    _VENDORED_DIR, COLLECTION_GAP, GitHubCollectorBase, RepoTree, RetryingTransport, get_threshold,
)

logger = logging.getLogger(__name__)

# Test layout and framework configuration, grouped so any variant counts once.
# Matched case-insensitively against a RepoTree (see base.py), so a single
# casing per candidate is enough -- AMReX-Codes/amrex's top-level "Tests"
# and superlu's "TESTING" both match "tests"/"testing" without every casing
# enumerated here (METRIC_BLIND_SPOTS.md class F1).
_TESTING_PATHS = {
    "Test suite directory": ["test", "tests", "testing", "src/test"],
    "CTest / CMake testing": ["CTestConfig.cmake", "cmake/CTestConfig.cmake"],
    "pytest configuration": ["pytest.ini", "tox.ini", "conftest.py", "setup.cfg"],
    "Unit-test framework": [
        "test/googletest", "extern/googletest", "third_party/googletest",
        "test/catch2", "extern/Catch2",
    ],
}

# Most projects declare testing inside a build or config file rather than
# with a dedicated one, so path matching alone found only SUNDIALS's test/
# directory (1/4) although it runs CTest (`include(CTest)` in
# cmake/SundialsSetupTesting.cmake), pytest (`[tool.pytest.ini_options]` in
# pyproject.toml) and GoogleTest (FetchContent). These labels fall back to
# the tree and then to the contents of a few build/config files.
_FRAMEWORK_DIR = r"(?:^|/)(?:googletest|gtest|catch2?|doctest|cmocka|pfunit)(?:/|$)"
_CONFTEST = r"(?:^|/)conftest\.py$"
_CONTENT_MARKERS = {
    "CTest / CMake testing": re.compile(r"^\s*(?:enable_testing\s*\(|include\s*\(\s*CTest\b)", re.M | re.I),
    "pytest configuration": re.compile(r"^\[tool(?:\.|:)pytest", re.M),
    "Unit-test framework": re.compile(
        r"FetchContent_Declare\s*\(\s*(?:googletest|catch2|doctest)\b"
        r"|find_package\s*\(\s*(?:GTest|Catch2|doctest)\b",
        re.I,
    ),
}
# CMake modules whose name mentions testing, read after the root CMakeLists.
_TEST_CMAKE_FILE = r"(?:^|/)[^/]*test[^/]*\.cmake$"
_MAX_TEST_CMAKE_FILES = 3
_CONFIG_FILES = ["CMakeLists.txt", "pyproject.toml", "setup.cfg"]

# Tooling that enforces consistency without a human in the loop.
_TOOLING_PATHS = {
    "Pre-commit hooks": [".pre-commit-config.yaml", ".pre-commit-config.yml"],
    "Code formatter config": [".clang-format", ".style.yapf", ".prettierrc", "rustfmt.toml"],
    "Linter config": [
        ".flake8", ".pylintrc", "ruff.toml", ".eslintrc.json",
        ".clang-tidy", ".editorconfig",
    ],
    "Dependency automation": [
        ".github/dependabot.yml", ".github/dependabot.yaml", "renovate.json",
    ],
}

# How many recently-closed PRs to sample for review coverage.
_PR_SAMPLE_SIZE = 50


class DevToolingCollector(GitHubCollectorBase):
    """Collects testing, review and tooling practices (Section 4.3.2)."""

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        repo_name = package.get("name", "Unknown")
        owner_repo = self._extract_owner_repo(package.get("repo_url", ""))
        if not owner_repo:
            logger.error(f"Could not extract owner/repo from {package.get('repo_url')}")
            return self._empty_result(repo_name)

        owner, repo = owner_repo
        logger.info(f"Collecting development tooling metrics for {repo_name}")

        async with httpx.AsyncClient(timeout=30.0, transport=RetryingTransport()) as client:
            tree, review = await asyncio.gather(
                RepoTree.fetch(client, self.github_headers, owner, repo),
                self._analyze_review_coverage(client, owner, repo),
                return_exceptions=True,
            )

        if isinstance(tree, Exception):
            logger.warning(f"COLLECTION-GAP category=testing,tooling reason=exception:{tree!r}")
            tree = COLLECTION_GAP
        testing = self._scan(tree, _TESTING_PATHS)
        if tree is not COLLECTION_GAP and testing["missing"]:
            async with httpx.AsyncClient(timeout=30.0, transport=RetryingTransport()) as client:
                testing = await self._refine_testing(client, owner, repo, tree, testing)
        tooling = self._scan(tree, _TOOLING_PATHS)
        if isinstance(review, Exception):
            logger.warning(f"COLLECTION-GAP category=code_review reason=exception:{review!r}")
            review = {"sampled": 0, "reviewed": 0, "coverage_pct": None, "not_collected": True}

        return {
            "package_name": repo_name,
            "repository": f"{owner}/{repo}",
            "timestamp": self._get_timestamp(),
            "testing": testing,
            "tooling": tooling,
            "code_review": review,
            "overall_score": self._calculate_score(testing, tooling, review),
        }

    def _scan(self, tree, groups: Dict[str, List[str]]) -> Dict[str, Any]:
        """Check each group against a RepoTree (or COLLECTION_GAP), recording
        the first matching path -- case-insensitively, and as either a file
        or a directory, since candidates like "test/googletest" name a
        vendored subdirectory (METRIC_BLIND_SPOTS.md class F1).
        """
        found, missing, not_collected, details = [], [], [], {}
        for label, paths in groups.items():
            if tree is COLLECTION_GAP:
                not_collected.append(label)
                details[label] = {"not_collected": True}
                continue
            url = tree.match_url(paths)
            if url:
                found.append(label)
                details[label] = {"exists": True, "url": url}
            else:
                missing.append(label)
                details[label] = {"exists": False}
        return {"found": found, "missing": missing, "not_collected": not_collected, "details": details}

    async def _refine_testing(
        self, client: httpx.AsyncClient, owner: str, repo: str, tree, testing: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Resolve testing labels the fixed paths missed, from the tree and
        then from build/config file contents. A label still missing after a
        read gapped is recorded as not collected rather than absent."""
        found, missing = list(testing["found"]), list(testing["missing"])
        not_collected, details = list(testing["not_collected"]), dict(testing["details"])

        def mark(label: str, path: str):
            missing.remove(label)
            found.append(label)
            details[label] = {"exists": True, "url": tree.url_for(path), "file": path}

        # Tree-only evidence first; a vendored copy of a test framework is
        # itself the signal, so that one isn't restricted to owned paths.
        if "Unit-test framework" in missing:
            dirs = {p[:m.end()].rstrip("/") for p in tree.find(_FRAMEWORK_DIR)
                    if (m := re.search(_FRAMEWORK_DIR, p, re.I))}
            if dirs:
                mark("Unit-test framework", min(dirs, key=lambda p: (p.count("/"), p)))
        if "pytest configuration" in missing:
            path = tree.find_owned(_CONFTEST)
            if path:
                mark("pytest configuration", path)

        wanted = [label for label in _CONTENT_MARKERS if label in missing]
        if not wanted:
            return {**testing, "found": found, "missing": missing, "details": details}

        cmake_modules = sorted(
            (p for p in tree.find(_TEST_CMAKE_FILE) if not _VENDORED_DIR.search(p)),
            key=lambda p: (p.count("/"), p),
        )[:_MAX_TEST_CMAKE_FILES]
        paths = [p for p in (tree.match([c]) for c in _CONFIG_FILES) if p] + cmake_modules

        async def read(path: str):
            data = await self._github_get(
                client, f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
            )
            if data is COLLECTION_GAP or data is None or not isinstance(data, dict):
                return data if data is COLLECTION_GAP else ""
            return base64.b64decode(data.get("content", "")).decode("utf-8", "replace")

        texts = await asyncio.gather(*[read(p) for p in paths])
        for label in wanted:
            for path, text in zip(paths, texts):
                if text and text is not COLLECTION_GAP and _CONTENT_MARKERS[label].search(text):
                    mark(label, path)
                    break
        if any(t is COLLECTION_GAP for t in texts):
            for label in [l for l in wanted if l in missing]:
                missing.remove(label)
                not_collected.append(label)
                details[label] = {"not_collected": True}
        return {"found": found, "missing": missing, "not_collected": not_collected, "details": details}

    async def _analyze_review_coverage(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> Dict[str, Any]:
        """Share of recently merged PRs that received at least one review.

        Self-approvals are not filtered out — GitHub reports them the same way,
        and distinguishing them would need per-review author comparison against
        the PR author for every sampled PR.
        """
        url = (
            f"https://api.github.com/repos/{owner}/{repo}/pulls"
            f"?state=closed&per_page={_PR_SAMPLE_SIZE}&sort=updated&direction=desc"
        )
        prs = await self._github_get(client, url)
        if prs is COLLECTION_GAP:
            return {"sampled": 0, "reviewed": 0, "coverage_pct": None, "not_collected": True}

        merged = [pr for pr in (prs or []) if pr.get("merged_at")]
        if not merged:
            return {"sampled": 0, "reviewed": 0, "coverage_pct": None}

        async def has_review(number: int):
            reviews = await self._github_get(
                client, f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}/reviews",
                params={"per_page": 1},
            )
            if reviews is COLLECTION_GAP:
                return COLLECTION_GAP
            return bool(reviews)

        flags = await asyncio.gather(*[has_review(pr["number"]) for pr in merged])
        reviewed = sum(1 for f in flags if f is True)
        # A PR whose review check gapped is dropped from the denominator --
        # it's neither confirmed reviewed nor confirmed unreviewed.
        gapped = sum(1 for f in flags if f is COLLECTION_GAP)
        sampled = len(merged) - gapped
        if sampled == 0:
            return {"sampled": 0, "reviewed": 0, "coverage_pct": None, "not_collected": True}
        return {
            "sampled": sampled,
            "reviewed": reviewed,
            "coverage_pct": round(reviewed / sampled * 100, 1),
        }

    def _calculate_score(self, testing: Dict, tooling: Dict, review: Dict) -> Dict[str, Any]:
        sub: Dict[str, Dict[str, Any]] = {}

        test_found = testing.get("found", [])
        test_passing = len(test_found) >= get_threshold("4.3.2", "Testing Framework Excellence")
        testing_entry: Dict[str, Any] = {
            "label": "Testing Framework Excellence",
            "value": f"{len(test_found)}/{len(_TESTING_PATHS)} indicators",
            "detail": ", ".join(test_found) if test_found else None,
            "passing": test_passing,
        }
        # A below-threshold count built on a gap isn't confirmed -- one of
        # the gapped categories could have pushed it over. A count that
        # already clears the threshold from confirmed data stands regardless.
        if not test_passing and testing.get("not_collected"):
            testing_entry["not_collected"] = True
        sub["testing_framework"] = testing_entry

        cov = review.get("coverage_pct")
        review_entry: Dict[str, Any] = {
            "label": "Code Review Quality Analysis",
            "value": f"{cov}% of {review.get('sampled', 0)} merged PRs reviewed"
                     if cov is not None else "No merged PRs to sample",
            "passing": cov is not None and cov >= get_threshold("4.3.2", "Code Review Quality Analysis"),
        }
        if review.get("not_collected"):
            review_entry["not_collected"] = True
        sub["code_review_quality"] = review_entry

        tool_found = tooling.get("found", [])
        tool_passing = len(tool_found) >= get_threshold("4.3.2", "Development Tool Integration")
        tooling_entry: Dict[str, Any] = {
            "label": "Development Tool Integration",
            "value": f"{len(tool_found)}/{len(_TOOLING_PATHS)} tools",
            "detail": ", ".join(tool_found) if tool_found else None,
            "passing": tool_passing,
        }
        if not tool_passing and tooling.get("not_collected"):
            tooling_entry["not_collected"] = True
        sub["dev_tool_integration"] = tooling_entry

        scorable = {k: v for k, v in sub.items() if not v.get("not_collected")}
        score = sum(1 for s in scorable.values() if s["passing"])
        max_score = len(scorable)
        if not max_score:
            return {
                "score": None, "max_score": 0, "percentage": None,
                "status": "not_collected", "sub_scores": sub,
            }
        return {
            "score": score,
            "max_score": max_score,
            "percentage": round(score / max_score * 100, 2),
            "sub_scores": sub,
        }

    def _empty_result(self, repo_name: str) -> Dict[str, Any]:
        empty = {"found": [], "missing": [], "not_collected": [], "details": {}}
        return {
            "package_name": repo_name,
            "repository": "unknown",
            "timestamp": self._get_timestamp(),
            "testing": empty,
            "tooling": empty,
            "code_review": {"sampled": 0, "reviewed": 0, "coverage_pct": None},
            "overall_score": {"score": 0, "max_score": 3, "percentage": 0, "sub_scores": {}},
        }
