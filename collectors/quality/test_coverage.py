"""
Test Coverage Collector (CASS Report Section 4.3.1 — Reliability and Robustness)

Fetches test coverage percentage from the Codecov public API. Codecov's v2
API returns coverage totals for public repositories without authentication:

    https://api.codecov.io/api/v2/github/{owner}/repos/{repo}/

Coveralls was evaluated as a second source but its public JSON endpoint
(coveralls.io/github/{owner}/{repo}.json) returns HTTP 403 for
unauthenticated, non-browser requests — it isn't usable here.

When there's no Codecov project, the CI configuration is checked for coverage
instrumentation: a project measuring coverage somewhere this can't read (a
CDash coverage build, for instance) is "not collected", not a confirmed
failure.
"""

import asyncio
import httpx
import logging
import re
from typing import Any, Dict, Optional

from collectors.ecosystem.base import COLLECTION_GAP, RepoTree, RetryingTransport
from forge.interface import Forge

logger = logging.getLogger(__name__)

_CODECOV_API = "https://api.codecov.io/api/v2/{provider}/{owner}/repos/{repo}/"

_CI_CONFIG = re.compile(r"^(?:\.github/workflows/[^/]+|\.gitlab-ci|\.gitlab/.+)\.ya?ml$", re.I)
_MAX_CI_FILES = 25
# Build options and tools that only appear when coverage is being measured.
_COVERAGE_IN_CI = re.compile(
    r"--coverage\b|-fprofile-arcs|\bgcovr\b|\blcov\b|\bllvm-cov\b|\bpytest-cov\b"
    r"|--cov(?:=|\s)|\bcoverage run\b|\bcodecov\b|\bcoveralls"
    # A coverage build type or build/job name: CMAKE_BUILD_TYPE=Coverage,
    # "debug-coverage", "coverage_test_linux".
    r"|CMAKE_BUILD_TYPE\W{0,3}Coverage\b|[-_]coverage(?=[-_\s\"':]|$)|\bcoverage[-_]"
    r"|\w*_COVERAGE=ON\b|\bENABLE_COVERAGE\b|\bCODE_COVERAGE\b",
    re.I,
)


class TestCoverageCollector:
    """Collects test coverage % via the public Codecov API (Section 4.3.1)."""

    def __init__(self, forge: Forge):
        self.forge = forge

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        repo_name = package.get("name", "Unknown")
        repo_url = package.get("repo_url", "")

        ref = self.forge.extract_ref(repo_url)
        if not ref:
            logger.error(f"Could not extract a repo reference from {repo_url}")
            return self._empty_result(repo_name)

        logger.info(f"Fetching test coverage for {ref}")

        async with httpx.AsyncClient(timeout=30.0) as client:
            result = await self._fetch_coverage(client, repo_name, ref)
        if not result["coverage_exists"] and result["repository"] != "unknown":
            async with httpx.AsyncClient(timeout=30.0, transport=RetryingTransport()) as client:
                result["coverage_in_ci"] = await self._coverage_in_ci(client, ref)
        return result

    async def _coverage_in_ci(self, client: httpx.AsyncClient, ref: str) -> Optional[str]:
        """The first CI config file that measures coverage, or None."""
        tree = await RepoTree.fetch(client, self.forge, ref)
        if tree is COLLECTION_GAP:
            return None
        paths = tree.find(_CI_CONFIG.pattern)[:_MAX_CI_FILES]
        texts = await asyncio.gather(*[self.forge.raw_text(client, ref, p) for p in paths])
        return next((p for p, t in zip(paths, texts) if t and _COVERAGE_IN_CI.search(t)), None)

    async def _fetch_coverage(
        self,
        client: httpx.AsyncClient,
        repo_name: str,
        ref: str,
    ) -> Dict[str, Any]:
        owner, repo = ref.split("/", 1)
        url = _CODECOV_API.format(provider=self.forge.platform, owner=owner, repo=repo)
        try:
            response = await client.get(url, headers={"Accept": "application/json"})
            if response.status_code == 404:
                logger.info(f"No Codecov project for {ref}")
                return self._no_coverage_result(repo_name, ref)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            logger.warning(f"Codecov fetch failed for {ref}: {e}")
            return self._empty_result(repo_name)

        totals = data.get("totals") or {}
        coverage = totals.get("coverage")

        if not data.get("active") or coverage is None:
            return self._no_coverage_result(repo_name, ref)

        return {
            "package_name": repo_name,
            "repository": ref,
            "timestamp": self.forge.get_timestamp(),
            "coverage_exists": True,
            "coverage_percentage": round(coverage, 1),
            "lines_covered": totals.get("hits"),
            "lines_total": totals.get("lines"),
            "source": "codecov",
            "coverage_url": f"https://codecov.io/gh/{ref}",
        }

    def _no_coverage_result(self, repo_name: str, ref: str) -> Dict[str, Any]:
        return {
            "package_name": repo_name,
            "repository": ref,
            "timestamp": self.forge.get_timestamp(),
            "coverage_exists": False,
            "coverage_percentage": None,
            "lines_covered": None,
            "lines_total": None,
            "source": None,
            "coverage_url": None,
        }

    def _empty_result(self, repo_name: str) -> Dict[str, Any]:
        return {
            "package_name": repo_name,
            "repository": "unknown",
            "timestamp": self.forge.get_timestamp(),
            "coverage_exists": False,
            "coverage_percentage": None,
            "lines_covered": None,
            "lines_total": None,
            "source": None,
            "coverage_url": None,
        }
