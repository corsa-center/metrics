"""
OpenSSF Scorecard Collector (CASS Report Section 4.2 — Sustainability)

Fetches the OpenSSF Scorecard score for a GitHub repository via the free
public API. Returns an overall score (0–10) plus per-check breakdowns.

API docs: https://api.securityscorecards.dev
"""

import httpx
import logging
from typing import Any, Dict, List, Optional

from collectors.sustainability.base import GitHubCollectorBase

logger = logging.getLogger(__name__)

_SCORECARD_API = "https://api.securityscorecards.dev/projects/github.com/{owner}/{repo}"


class OpenSSFScorecardCollector(GitHubCollectorBase):
    """Collects OpenSSF Scorecard metrics via the public Scorecard API."""

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        repo_name = package.get("name", "Unknown")
        repo_url = package.get("repo_url", "")

        owner_repo = self._extract_owner_repo(repo_url)
        if not owner_repo:
            logger.error(f"Could not extract owner/repo from {repo_url}")
            return self._empty_result(repo_name)

        owner, repo = owner_repo
        logger.info(f"Fetching OpenSSF Scorecard for {owner}/{repo}")

        async with httpx.AsyncClient(timeout=30.0) as client:
            return await self._fetch_scorecard(client, repo_name, owner, repo)

    async def _fetch_scorecard(
        self,
        client: httpx.AsyncClient,
        repo_name: str,
        owner: str,
        repo: str,
    ) -> Dict[str, Any]:
        url = _SCORECARD_API.format(owner=owner, repo=repo)
        try:
            response = await client.get(
                url,
                headers={"Accept": "application/json"},
                follow_redirects=True,
            )
            if response.status_code == 404:
                logger.info(f"No Scorecard found for {owner}/{repo}")
                return self._no_scorecard_result(repo_name, owner, repo)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as e:
            logger.warning(f"Scorecard API error for {owner}/{repo}: {e}")
            return self._empty_result(repo_name)
        except Exception as e:
            logger.error(f"Scorecard fetch failed for {owner}/{repo}: {e}")
            return self._empty_result(repo_name)

        score: float = data.get("score", 0.0)
        checks: List[Dict[str, Any]] = data.get("checks", [])

        check_details = {
            c["name"]: {
                "score": c.get("score", 0),
                "reason": c.get("reason", ""),
                "documentation_url": c.get("documentation", {}).get("url", ""),
            }
            for c in checks
        }

        passed = sum(1 for c in checks if c.get("score", 0) >= 7)

        return {
            "package_name": repo_name,
            "repository": f"{owner}/{repo}",
            "timestamp": self._get_timestamp(),
            "scorecard_exists": True,
            "score": round(score, 1),
            "max_score": 10.0,
            "percentage": round(score * 10, 1),
            "checks_total": len(checks),
            "checks_passed": passed,
            "checks": check_details,
            "scorecard_url": f"https://scorecard.dev/viewer/?uri=github.com/{owner}/{repo}",
        }

    def _no_scorecard_result(
        self, repo_name: str, owner: str, repo: str
    ) -> Dict[str, Any]:
        return {
            "package_name": repo_name,
            "repository": f"{owner}/{repo}",
            "timestamp": self._get_timestamp(),
            "scorecard_exists": False,
            "score": None,
            "max_score": 10.0,
            "percentage": None,
            "checks_total": 0,
            "checks_passed": 0,
            "checks": {},
            "scorecard_url": None,
            "recommendation": f"Run Scorecard via: https://scorecard.dev/viewer/?uri=github.com/{owner}/{repo}",
        }

    def _empty_result(self, repo_name: str) -> Dict[str, Any]:
        return {
            "package_name": repo_name,
            "repository": "unknown",
            "timestamp": self._get_timestamp(),
            "scorecard_exists": False,
            "score": None,
            "max_score": 10.0,
            "percentage": None,
            "checks_total": 0,
            "checks_passed": 0,
            "checks": {},
            "scorecard_url": None,
        }
