"""
Static Analysis / CodeQL Collector (CASS Report Section 4.3.1 — Enhanced Security Analysis)

Detects whether a repository runs GitHub CodeQL code scanning by checking
for a CodeQL workflow file. GitHub's code-scanning alerts API
(/repos/{owner}/{repo}/code-scanning/alerts) requires authentication even
for public repos (returns 401 unauthenticated), so this uses the same
workflow-presence proxy pattern as
collectors/sustainability/openssf_badge.py rather than fetching alert counts.
"""

import httpx
import logging
from typing import Any, Dict, List

from collectors.sustainability.base import GitHubCollectorBase

logger = logging.getLogger(__name__)

_CODEQL_WORKFLOW_PATHS: List[str] = [
    ".github/workflows/codeql.yml",
    ".github/workflows/codeql.yaml",
    ".github/workflows/codeql-analysis.yml",
    ".github/workflows/codeql-analysis.yaml",
]


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

        return {
            "package_name": repo_name,
            "repository": f"{owner}/{repo}",
            "timestamp": self._get_timestamp(),
            "has_codeql": False,
            "workflow_file": None,
            "workflow_url": None,
        }

    def _empty_result(self, repo_name: str) -> Dict[str, Any]:
        return {
            "package_name": repo_name,
            "repository": "unknown",
            "timestamp": self._get_timestamp(),
            "has_codeql": False,
            "workflow_file": None,
            "workflow_url": None,
        }
