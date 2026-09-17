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
import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx

from collectors.ecosystem.base import COLLECTION_GAP, GitHubCollectorBase, RetryingTransport, get_threshold

logger = logging.getLogger(__name__)

# Test layout and framework configuration, grouped so any variant counts once.
_TESTING_PATHS = {
    "Test suite directory": ["test", "tests", "testing", "src/test"],
    "CTest / CMake testing": ["CTestConfig.cmake", "cmake/CTestConfig.cmake"],
    "pytest configuration": ["pytest.ini", "tox.ini", "conftest.py", "setup.cfg"],
    "Test framework vendored": [
        "test/googletest", "extern/googletest", "third_party/googletest",
        "test/catch2", "extern/Catch2",
    ],
}

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
            testing, tooling, review = await asyncio.gather(
                self._scan(client, owner, repo, _TESTING_PATHS),
                self._scan(client, owner, repo, _TOOLING_PATHS),
                self._analyze_review_coverage(client, owner, repo),
                return_exceptions=True,
            )

        empty_scan = {"found": [], "missing": [], "not_collected": [], "details": {}}
        if isinstance(testing, Exception):
            logger.warning(f"COLLECTION-GAP category=testing reason=exception:{testing!r}")
            testing = empty_scan
        if isinstance(tooling, Exception):
            logger.warning(f"COLLECTION-GAP category=tooling reason=exception:{tooling!r}")
            tooling = empty_scan
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

    async def _scan(
        self, client: httpx.AsyncClient, owner: str, repo: str, groups: Dict[str, List[str]]
    ) -> Dict[str, Any]:
        """Check each group, recording the first matching path."""

        async def check(label: str, paths: List[str]) -> Tuple[str, Optional[str], bool]:
            saw_gap = False
            for path in paths:
                url = await self._check_file_exists(client, owner, repo, path)
                if url is COLLECTION_GAP:
                    saw_gap = True
                    continue
                if url:
                    return label, url, saw_gap
            return label, None, saw_gap

        results = await asyncio.gather(*[check(l, p) for l, p in groups.items()])
        found, missing, not_collected, details = [], [], [], {}
        for label, url, saw_gap in results:
            if url:
                found.append(label)
                details[label] = {"exists": True, "url": url}
            elif saw_gap:
                not_collected.append(label)
                details[label] = {"not_collected": True}
            else:
                missing.append(label)
                details[label] = {"exists": False}
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
