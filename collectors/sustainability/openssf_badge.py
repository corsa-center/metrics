"""
OpenSSF Best Practices Badge Collector (CASS Report Section 4.2.5)

Checks whether a project has an OpenSSF Best Practices Badge and reports
badge level and criteria completion. When no badge exists, scans the
repository to identify which governance, security, and quality requirements
are already met.

OpenSSF Badge API: https://bestpractices.coreinfrastructure.org/projects.json
"""

import asyncio
import httpx
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from collectors.sustainability.base import GitHubCollectorBase

logger = logging.getLogger(__name__)


class OpenSSFBadgeCollector(GitHubCollectorBase):
    """Collects OpenSSF Best Practices Badge metrics (Section 4.2.5)."""

    BADGE_SEARCH_URL = "https://bestpractices.coreinfrastructure.org/projects.json"

    # File patterns checked when no badge exists.
    # These overlap intentionally with CommunityHealthCollector — OpenSSF uses
    # the same artifacts to award badge criteria.
    GOVERNANCE_FILES: Dict[str, List[str]] = {
        "code_of_conduct": [
            "CODE_OF_CONDUCT.md",
            "CODE_OF_CONDUCT.txt",
            "CODE-OF-CONDUCT.md",
            "code_of_conduct.md",
            "docs/CODE_OF_CONDUCT.md",
            ".github/CODE_OF_CONDUCT.md",
        ],
        "governance": [
            "GOVERNANCE.md",
            "GOVERNANCE.txt",
            "governance.md",
            "docs/GOVERNANCE.md",
            ".github/GOVERNANCE.md",
        ],
        "contributing": [
            "CONTRIBUTING.md",
            "CONTRIBUTING.txt",
            "contributing.md",
            "docs/CONTRIBUTING.md",
            ".github/CONTRIBUTING.md",
        ],
    }

    SECURITY_FILES: Dict[str, List[str]] = {
        "security_policy": [
            "SECURITY.md",
            "SECURITY.txt",
            "security.md",
            "docs/SECURITY.md",
            ".github/SECURITY.md",
        ],
        "vulnerability_process": [
            "SECURITY.md",
            ".github/SECURITY.md",
            "docs/security.md",
        ],
    }

    QUALITY_FILES: Dict[str, List[str]] = {
        "ci_cd": [
            ".github/workflows",
            ".travis.yml",
            ".circleci/config.yml",
            ".gitlab-ci.yml",
            "Jenkinsfile",
        ],
        "documentation": [
            "README.md",
            "docs/README.md",
            "README",
        ],
        "testing": [
            "test/",
            "tests/",
            "spec/",
            ".github/workflows",
        ],
    }

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        repo_name = package.get("name", "Unknown")
        repo_url = package.get("repo_url", "")

        logger.info(f"Collecting OpenSSF Badge metrics for {repo_name}")

        owner_repo = self._extract_owner_repo(repo_url)
        if not owner_repo:
            logger.error(f"Could not extract owner/repo from {repo_url}")
            return self._empty_result(repo_name)

        owner, repo = owner_repo

        async with httpx.AsyncClient(timeout=30.0) as client:
            badge_data = await self._search_badge(client, owner, repo, repo_url)
            if badge_data:
                logger.info(f"Badge found — level: {badge_data.get('badge_level')}, progress: {badge_data.get('badge_percentage_0', 0)}%")
                return self._collect_with_badge(repo_name, owner, repo, badge_data)
            else:
                logger.info("No badge found — scanning repository for requirements")
                return await self._collect_without_badge(client, repo_name, owner, repo)

    # ------------------------------------------------------------------ #
    # Badge vs. scan paths                                                 #
    # ------------------------------------------------------------------ #

    def _collect_with_badge(
        self, repo_name: str, owner: str, repo: str, badge_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        badge_level = self._get_badge_level(badge_data)
        badge_percentage = badge_data.get("badge_percentage_0", 0)
        badge_id = badge_data.get("id")
        is_in_progress = badge_percentage > 0 and badge_percentage < 100

        return {
            "package_name": repo_name,
            "repository": f"{owner}/{repo}",
            "timestamp": self._get_timestamp(),
            "badge_exists": True,
            "badge_status": {
                "level": badge_level,
                "id": badge_id,
                "url": f"https://www.bestpractices.dev/projects/{badge_id}",
                "progress_percentage": badge_percentage,
                "in_progress": is_in_progress,
                "started": badge_percentage > 0,
            },
            "governance_criteria": self._assess_governance_from_badge(badge_data),
            "security_criteria": self._assess_security_from_badge(badge_data),
            "quality_criteria": self._assess_quality_from_badge(badge_data),
            "overall_score": {
                "score": round(badge_percentage, 2),
                "max_score": 100,
                "percentage": round(badge_percentage, 2),
                "status": "passing" if badge_percentage >= 100 else "in_progress" if badge_percentage > 0 else "not_started",
            },
            "assessment_method": "openssf_badge_api",
        }

    async def _collect_without_badge(
        self, client: httpx.AsyncClient, repo_name: str, owner: str, repo: str
    ) -> Dict[str, Any]:
        governance = await self._scan_files(client, owner, repo, self.GOVERNANCE_FILES)
        security = await self._scan_files(client, owner, repo, self.SECURITY_FILES)
        quality = await self._scan_files(client, owner, repo, self.QUALITY_FILES)

        overall_pct = (
            governance.get("percentage", 0) * 0.4
            + security.get("percentage", 0) * 0.3
            + quality.get("percentage", 0) * 0.3
        )

        return {
            "package_name": repo_name,
            "repository": f"{owner}/{repo}",
            "timestamp": self._get_timestamp(),
            "badge_exists": False,
            "badge_status": {
                "level": "none",
                "id": None,
                "url": None,
                "progress_percentage": 0,
                "in_progress": False,
                "started": False,
            },
            "governance_criteria": governance,
            "security_criteria": security,
            "quality_criteria": quality,
            "overall_score": {
                "score": round(overall_pct, 2),
                "max_score": 100,
                "percentage": round(overall_pct, 2),
                "status": "not_started",
                "estimated": True,
            },
            "assessment_method": "repository_scan",
            "recommendation": "Start OpenSSF Badge at: https://www.bestpractices.dev/projects/new",
        }

    # ------------------------------------------------------------------ #
    # HTTP helpers                                                         #
    # ------------------------------------------------------------------ #

    async def _search_badge(
        self, client: httpx.AsyncClient, owner: str, repo: str, repo_url: str
    ) -> Optional[Dict[str, Any]]:
        headers = {"Accept": "application/json", "User-Agent": "CASS-Metrics-Collector"}
        try:
            response = await client.get(self.BADGE_SEARCH_URL, params={"url": repo_url}, headers=headers, follow_redirects=True)
            if response.status_code == 200:
                results = response.json()
                if results:
                    return results[0]

            for query in [f"github.com/{owner}/{repo}", f"{owner}/{repo}"]:
                response = await client.get(self.BADGE_SEARCH_URL, params={"q": query}, headers=headers, follow_redirects=True)
                if response.status_code == 200:
                    for result in response.json():
                        if f"{owner}/{repo}".lower() in result.get("repo_url", "").lower():
                            return result
        except Exception as e:
            logger.debug(f"Error searching for badge: {e}")
        return None

    async def _scan_files(
        self,
        client: httpx.AsyncClient,
        owner: str,
        repo: str,
        file_map: Dict[str, List[str]],
    ) -> Dict[str, Any]:
        """Check each criterion in file_map against the repository."""
        found: List[str] = []
        missing: List[str] = []
        details: Dict[str, Any] = {}

        for criterion, patterns in file_map.items():
            for pattern in patterns:
                if await self._check_file_exists(client, owner, repo, pattern):
                    found.append(criterion)
                    details[criterion] = {
                        "exists": True,
                        "file": pattern,
                        "url": f"https://github.com/{owner}/{repo}/blob/main/{pattern}",
                    }
                    logger.info(f"  {criterion}: {pattern}")
                    break
            else:
                missing.append(criterion)
                details[criterion] = {"exists": False, "recommended": patterns[0]}

        count_total = len(file_map)
        return {
            "found": found,
            "missing": missing,
            "details": details,
            "count_found": len(found),
            "count_total": count_total,
            "percentage": round((len(found) / count_total) * 100, 2) if count_total else 0,
        }

    # ------------------------------------------------------------------ #
    # Pure computation helpers                                             #
    # ------------------------------------------------------------------ #

    def _assess_governance_from_badge(self, badge_data: Dict[str, Any]) -> Dict[str, Any]:
        return self._assess_criteria_from_badge(
            badge_data, ["governance", "contribution", "contribution_requirements", "code_of_conduct"]
        )

    def _assess_security_from_badge(self, badge_data: Dict[str, Any]) -> Dict[str, Any]:
        return self._assess_criteria_from_badge(
            badge_data, ["vulnerability_report_process", "vulnerability_report_private", "security_policy"]
        )

    def _assess_quality_from_badge(self, badge_data: Dict[str, Any]) -> Dict[str, Any]:
        return self._assess_criteria_from_badge(
            badge_data, ["test", "test_continuous_integration", "documentation_basics"]
        )

    def _assess_criteria_from_badge(
        self, badge_data: Dict[str, Any], criteria: List[str]
    ) -> Dict[str, Any]:
        met = []
        unmet = []
        details: Dict[str, Any] = {}
        for criterion in criteria:
            status = badge_data.get(f"{criterion}_status", "Unknown")
            is_met = status == "Met"
            (met if is_met else unmet).append(criterion)
            details[criterion] = {"status": status, "met": is_met}
        return {
            "found": met,
            "missing": unmet,
            "details": details,
            "count_found": len(met),
            "count_total": len(criteria),
            "percentage": round((len(met) / len(criteria)) * 100, 2) if criteria else 0,
        }

    def _get_badge_level(self, badge_data: Dict[str, Any]) -> str:
        """Map numeric badge_level field to a human-readable string."""
        level_map = {"2": "gold", "1": "silver", "0": "passing"}
        level = level_map.get(str(badge_data.get("badge_level", "")))
        if level:
            return level
        return "in_progress" if badge_data.get("badge_percentage_0", 0) > 0 else "none"

    def _empty_result(self, repo_name: str) -> Dict[str, Any]:
        return {
            "package_name": repo_name,
            "repository": "unknown",
            "timestamp": self._get_timestamp(),
            "badge_exists": False,
            "badge_status": {"level": "none", "id": None, "url": None, "progress_percentage": 0, "in_progress": False, "started": False},
            "governance_criteria": {"found": [], "missing": [], "percentage": 0},
            "security_criteria": {"found": [], "missing": [], "percentage": 0},
            "quality_criteria": {"found": [], "missing": [], "percentage": 0},
            "overall_score": {"score": 0, "max_score": 100, "percentage": 0, "status": "error"},
            "assessment_method": "error",
        }
