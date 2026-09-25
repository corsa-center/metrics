"""
Static Analysis / CodeQL Collector (CASS Report Section 4.3.1 — Enhanced Security Analysis)

Detects whether a repository runs GitHub CodeQL code scanning, either from a
CodeQL workflow file or from GitHub's "default setup", which is enabled in
repository settings and leaves no file in the tree -- it only appears in the
Actions workflows list, under a dynamic/github-code-scanning/ path. GitHub's
code-scanning alerts API
(/repos/{owner}/{repo}/code-scanning/alerts) requires authentication even
for public repos (returns 401 unauthenticated), so this uses the same
workflow-presence proxy pattern as
collectors/ecosystem/openssf_badge.py rather than fetching alert counts.
"""

import asyncio
import httpx
import logging
from typing import Any, Dict, List, Optional

from collectors.ecosystem.base import COLLECTION_GAP, GitHubCollectorBase, RetryingTransport

logger = logging.getLogger(__name__)

_CODEQL_WORKFLOW_PATHS: List[str] = [
    ".github/workflows/codeql.yml",
    ".github/workflows/codeql.yaml",
    ".github/workflows/codeql-analysis.yml",
    ".github/workflows/codeql-analysis.yaml",
]

_DEFAULT_SETUP_PATH_PREFIX = "dynamic/github-code-scanning/codeql"

_WORKFLOWS_DIR = ".github/workflows"
# Bounds worst-case API calls per repo when falling back to a content scan.
_MAX_WORKFLOWS_TO_SCAN = 25


class StaticAnalysisCollector(GitHubCollectorBase):
    """Detects CodeQL / static analysis security scanning (Section 4.3.1)."""

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        repo_name = package.get("name", "Unknown")
        repo_url = package.get("repo_url", "")

        owner_repo = self._extract_owner_repo(repo_url)
        if not owner_repo:
            logger.error(f"Could not extract owner/repo from {repo_url}")
            return self._empty_result(repo_name)

        owner, repo = owner_repo
        logger.info(f"Checking CodeQL / static analysis for {owner}/{repo}")

        async with httpx.AsyncClient(timeout=30.0, transport=RetryingTransport()) as client:
            saw_gap = False
            for path in _CODEQL_WORKFLOW_PATHS:
                html_url = await self._check_file_exists(client, owner, repo, path)
                if html_url is COLLECTION_GAP:
                    saw_gap = True
                    continue
                if html_url:
                    return {
                        "package_name": repo_name,
                        "repository": f"{owner}/{repo}",
                        "timestamp": self._get_timestamp(),
                        "has_codeql": True,
                        "workflow_file": path,
                        "workflow_url": html_url,
                    }

            default_setup, setup_gap = await self._find_default_setup(client, owner, repo)
            if default_setup:
                return {
                    "package_name": repo_name,
                    "repository": f"{owner}/{repo}",
                    "timestamp": self._get_timestamp(),
                    "has_codeql": True,
                    "workflow_file": "CodeQL default setup",
                    "workflow_url": default_setup,
                }
            saw_gap = saw_gap or setup_gap

            # None of the common filenames matched — some projects bundle CodeQL
            # into a differently-named workflow (e.g. ADIOS2's `everything.yml`).
            # Fall back to scanning workflow file contents for a codeql-action
            # reference, since filename guessing alone produces false negatives.
            found, scan_gap = await self._scan_workflows_for_codeql(client, owner, repo)
            if found:
                return {
                    "package_name": repo_name,
                    "repository": f"{owner}/{repo}",
                    "timestamp": self._get_timestamp(),
                    "has_codeql": True,
                    "workflow_file": found["file"],
                    "workflow_url": found["url"],
                }
            saw_gap = saw_gap or scan_gap

        result = {
            "package_name": repo_name,
            "repository": f"{owner}/{repo}",
            "timestamp": self._get_timestamp(),
            "has_codeql": False,
            "workflow_file": None,
            "workflow_url": None,
        }
        # A False here built on a gap isn't a confirmed "no CodeQL" -- the
        # gap could be hiding the workflow file that would have matched.
        if saw_gap:
            result["not_collected"] = True
        return result

    async def _find_default_setup(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> tuple:
        """Actions page URL of an active CodeQL default setup, or None.

        Returns (url_or_None, saw_gap). A disabled default setup doesn't count.
        """
        data = await self._github_get(
            client, f"https://api.github.com/repos/{owner}/{repo}/actions/workflows",
            params={"per_page": 100},
        )
        if data is COLLECTION_GAP:
            return None, True
        for wf in (data or {}).get("workflows", []):
            if wf.get("path", "").startswith(_DEFAULT_SETUP_PATH_PREFIX) and wf.get("state") == "active":
                return wf.get("html_url") or f"https://github.com/{owner}/{repo}/actions", False
        return None, False

    async def _scan_workflows_for_codeql(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> tuple:
        """Scan workflow file contents for a `codeql-action` reference.

        Returns (match_or_None, saw_gap).
        """
        entries = await self._github_get(
            client, f"https://api.github.com/repos/{owner}/{repo}/contents/{_WORKFLOWS_DIR}"
        )
        if entries is COLLECTION_GAP:
            return None, True
        if not isinstance(entries, list):
            return None, False

        yaml_files = [
            e for e in entries if e.get("name", "").endswith((".yml", ".yaml"))
        ][:_MAX_WORKFLOWS_TO_SCAN]

        async def check_file(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            download_url = entry.get("download_url")
            if not download_url:
                return None
            try:
                resp = await client.get(download_url)
            except Exception as e:
                logger.debug(f"COLLECTION-GAP workflow={entry.get('name')} reason=exception:{e!r}")
                return COLLECTION_GAP
            if resp.status_code != 200:
                return COLLECTION_GAP
            if "codeql-action" in resp.text:
                return {
                    "file": f"{_WORKFLOWS_DIR}/{entry['name']}",
                    "url": entry.get("html_url", download_url),
                }
            return None

        results = await asyncio.gather(*[check_file(e) for e in yaml_files])
        saw_gap = any(r is COLLECTION_GAP for r in results)
        for result in results:
            if result and result is not COLLECTION_GAP:
                return result, saw_gap
        return None, saw_gap

    def _empty_result(self, repo_name: str) -> Dict[str, Any]:
        return {
            "package_name": repo_name,
            "repository": "unknown",
            "timestamp": self._get_timestamp(),
            "has_codeql": False,
            "workflow_file": None,
            "workflow_url": None,
        }
