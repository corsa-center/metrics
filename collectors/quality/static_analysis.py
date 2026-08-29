"""
Static Analysis / CodeQL Collector (CASS Report Section 4.3.1 — Enhanced Security Analysis)

Detects whether a repository runs GitHub CodeQL code scanning by checking
for a CodeQL workflow file. GitHub's code-scanning alerts API
(/repos/{owner}/{repo}/code-scanning/alerts) requires authentication even
for public repos (returns 401 unauthenticated), so this uses the same
workflow-presence proxy pattern as
collectors/sustainability/openssf_badge.py rather than fetching alert counts.
"""

import asyncio
import httpx
import logging
from typing import Any, Dict, List, Optional

from collectors.sustainability.base import GitHubCollectorBase

logger = logging.getLogger(__name__)

_CODEQL_WORKFLOW_PATHS: List[str] = [
    ".github/workflows/codeql.yml",
    ".github/workflows/codeql.yaml",
    ".github/workflows/codeql-analysis.yml",
    ".github/workflows/codeql-analysis.yaml",
]

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

        async with httpx.AsyncClient(timeout=30.0) as client:
            for path in _CODEQL_WORKFLOW_PATHS:
                html_url = await self._check_file_exists(client, owner, repo, path)
                if html_url:
                    return {
                        "package_name": repo_name,
                        "repository": f"{owner}/{repo}",
                        "timestamp": self._get_timestamp(),
                        "has_codeql": True,
                        "workflow_file": path,
                        "workflow_url": html_url,
                    }

            # None of the common filenames matched — some projects bundle CodeQL
            # into a differently-named workflow (e.g. ADIOS2's `everything.yml`).
            # Fall back to scanning workflow file contents for a codeql-action
            # reference, since filename guessing alone produces false negatives.
            found = await self._scan_workflows_for_codeql(client, owner, repo)
            if found:
                return {
                    "package_name": repo_name,
                    "repository": f"{owner}/{repo}",
                    "timestamp": self._get_timestamp(),
                    "has_codeql": True,
                    "workflow_file": found["file"],
                    "workflow_url": found["url"],
                }

        return {
            "package_name": repo_name,
            "repository": f"{owner}/{repo}",
            "timestamp": self._get_timestamp(),
            "has_codeql": False,
            "workflow_file": None,
            "workflow_url": None,
        }

    async def _scan_workflows_for_codeql(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> Optional[Dict[str, str]]:
        """Scan workflow file contents for a `codeql-action` reference."""
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{_WORKFLOWS_DIR}"
        try:
            response = await client.get(url, headers=self.github_headers)
            if response.status_code != 200:
                return None
            entries = response.json()
            if not isinstance(entries, list):
                return None
        except Exception as e:
            logger.debug(f"Error listing workflows for {owner}/{repo}: {e}")
            return None

        yaml_files = [
            e for e in entries if e.get("name", "").endswith((".yml", ".yaml"))
        ][:_MAX_WORKFLOWS_TO_SCAN]

        async def check_file(entry: Dict[str, Any]) -> Optional[Dict[str, str]]:
            download_url = entry.get("download_url")
            if not download_url:
                return None
            try:
                resp = await client.get(download_url)
                if resp.status_code == 200 and "codeql-action" in resp.text:
                    return {
                        "file": f"{_WORKFLOWS_DIR}/{entry['name']}",
                        "url": entry.get("html_url", download_url),
                    }
            except Exception as e:
                logger.debug(f"Error fetching workflow {entry.get('name')}: {e}")
            return None

        for result in await asyncio.gather(*[check_file(e) for e in yaml_files]):
            if result:
                return result
        return None

    def _empty_result(self, repo_name: str) -> Dict[str, Any]:
        return {
            "package_name": repo_name,
            "repository": "unknown",
            "timestamp": self._get_timestamp(),
            "has_codeql": False,
            "workflow_file": None,
            "workflow_url": None,
        }
