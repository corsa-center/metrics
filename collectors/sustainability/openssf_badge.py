"""
OpenSSF Best Practices Badge Collector

Collects metrics related to OpenSSF Best Practices Badge including:
- Badge status (passing, silver, gold, in_progress, none)
- Governance documentation requirements
- Security practices
- Quality assurance
- Overall badge completion status

OpenSSF Best Practices Badge API: https://bestpractices.coreinfrastructure.org/projects.json
"""

import asyncio
import httpx
import logging
from typing import Dict, Any, Optional, List
from pathlib import Path
import re

logger = logging.getLogger(__name__)


class OpenSSFBadgeCollector:
    """Collects OpenSSF Best Practices Badge metrics from GitHub repositories"""

    # OpenSSF Badge API endpoints
    BADGE_API_BASE = "https://bestpractices.coreinfrastructure.org"
    BADGE_SEARCH_URL = f"{BADGE_API_BASE}/projects.json"

    # Governance-related criteria from OpenSSF Badge
    GOVERNANCE_CRITERIA = [
        "governance",
        "contribution",
        "contribution_requirements",
        "governance_doc",
        "code_of_conduct",
        "governance_implementation",
    ]

    # Security-related criteria
    SECURITY_CRITERIA = [
        "vulnerability_report_process",
        "vulnerability_report_private",
        "vulnerability_report_response",
        "security_policy",
    ]

    # Quality-related criteria
    QUALITY_CRITERIA = [
        "test",
        "test_invocation",
        "test_most",
        "test_continuous_integration",
        "documentation_basics",
        "documentation_interface",
    ]

    def __init__(self, github_token: Optional[str] = None):
        """
        Initialize collector with optional GitHub token

        Args:
            github_token: Optional GitHub token for API access
        """
        self.github_token = github_token
        self.headers = {
            "Accept": "application/json",
            "User-Agent": "CASS-Metrics-Collector"
        }
        if github_token:
            self.github_headers = {
                "Authorization": f"token {github_token}",
                "Accept": "application/vnd.github.v3+json"
            }
        else:
            self.github_headers = {}

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        """
        Collect OpenSSF Badge metrics for a package

        Args:
            package: Dictionary with 'name' and 'repo_url' keys

        Returns:
            Dictionary with OpenSSF Badge metrics
        """
        repo_name = package.get("name", "Unknown")
        repo_url = package.get("repo_url", "")

        logger.info(f"Collecting OpenSSF Badge metrics for {repo_name}")

        # Extract owner/repo from URL
        owner_repo = self._extract_owner_repo(repo_url)
        if not owner_repo:
            logger.error(f"Could not extract owner/repo from {repo_url}")
            return self._empty_result(repo_name)

        owner, repo = owner_repo

        # Search for badge data
        badge_data = await self._search_badge(owner, repo, repo_url)

        if not badge_data:
            logger.info(f"No OpenSSF Badge found for {repo_name}")
            return self._no_badge_result(repo_name, f"{owner}/{repo}")

        # Parse badge details
        badge_level = self._get_badge_level(badge_data)
        governance_score = self._assess_governance(badge_data)
        security_score = self._assess_security(badge_data)
        quality_score = self._assess_quality(badge_data)

        # Get detailed criteria status
        criteria_details = self._extract_criteria_details(badge_data)

        return {
            "package_name": repo_name,
            "repository": f"{owner}/{repo}",
            "timestamp": self._get_timestamp(),
            "badge_status": {
                "has_badge": True,
                "badge_level": badge_level,
                "badge_id": badge_data.get("id"),
                "badge_url": f"{self.BADGE_API_BASE}/projects/{badge_data.get('id')}",
                "percentage": badge_data.get("badge_percentage_0", 0),
            },
            "governance_criteria": governance_score,
            "security_criteria": security_score,
            "quality_criteria": quality_score,
            "criteria_details": criteria_details,
            "overall_score": self._calculate_score(
                badge_level, governance_score, security_score, quality_score
            ),
        }

    async def _search_badge(
        self, owner: str, repo: str, repo_url: str
    ) -> Optional[Dict[str, Any]]:
        """
        Search for OpenSSF Badge data for a repository

        Args:
            owner: Repository owner
            repo: Repository name
            repo_url: Full repository URL

        Returns:
            Badge data dictionary if found, None otherwise
        """
        logger.info(f"Searching for OpenSSF Badge for {owner}/{repo}")

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Search by repository URL
                params = {"url": repo_url}
                response = await client.get(
                    self.BADGE_SEARCH_URL,
                    params=params,
                    headers=self.headers,
                    follow_redirects=True
                )

                if response.status_code == 200:
                    results = response.json()
                    if results and len(results) > 0:
                        logger.info(f"Found badge data for {owner}/{repo}")
                        return results[0]  # Return first match

                # Try alternative search by repository name
                search_queries = [
                    f"github.com/{owner}/{repo}",
                    f"{owner}/{repo}",
                    repo,
                ]

                for query in search_queries:
                    params = {"q": query}
                    response = await client.get(
                        self.BADGE_SEARCH_URL,
                        params=params,
                        headers=self.headers,
                        follow_redirects=True
                    )

                    if response.status_code == 200:
                        results = response.json()
                        # Filter results to match our repo
                        for result in results:
                            result_url = result.get("repo_url", "")
                            if f"{owner}/{repo}" in result_url.lower():
                                logger.info(f"Found badge data via search for {owner}/{repo}")
                                return result

                logger.debug(f"No badge found for {owner}/{repo}")
                return None

        except Exception as e:
            logger.warning(f"Error searching for badge: {e}")
            return None

    def _get_badge_level(self, badge_data: Dict[str, Any]) -> str:
        """
        Determine badge level from badge data

        Args:
            badge_data: Badge data from API

        Returns:
            Badge level: "passing", "silver", "gold", "in_progress", or "none"
        """
        if badge_data.get("badge_level") == "2":
            return "gold"
        elif badge_data.get("badge_level") == "1":
            return "silver"
        elif badge_data.get("badge_level") == "0":
            return "passing"
        else:
            # Check if in progress
            percentage = badge_data.get("badge_percentage_0", 0)
            if percentage > 0:
                return "in_progress"
            return "none"

    def _assess_governance(self, badge_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Assess governance-related criteria

        Args:
            badge_data: Badge data from API

        Returns:
            Dictionary with governance assessment
        """
        met_count = 0
        total_count = len(self.GOVERNANCE_CRITERIA)
        details = []

        for criterion in self.GOVERNANCE_CRITERIA:
            status = badge_data.get(criterion + "_status")
            justification = badge_data.get(criterion + "_justification", "")

            if status == "Met":
                met_count += 1
                details.append({
                    "criterion": criterion,
                    "status": "Met",
                    "met": True
                })
            else:
                details.append({
                    "criterion": criterion,
                    "status": status or "Unknown",
                    "met": False
                })

        return {
            "met": met_count,
            "total": total_count,
            "percentage": round((met_count / total_count) * 100, 2) if total_count > 0 else 0,
            "details": details,
        }

    def _assess_security(self, badge_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Assess security-related criteria

        Args:
            badge_data: Badge data from API

        Returns:
            Dictionary with security assessment
        """
        met_count = 0
        total_count = len(self.SECURITY_CRITERIA)
        details = []

        for criterion in self.SECURITY_CRITERIA:
            status = badge_data.get(criterion + "_status")

            if status == "Met":
                met_count += 1
                details.append({
                    "criterion": criterion,
                    "status": "Met",
                    "met": True
                })
            else:
                details.append({
                    "criterion": criterion,
                    "status": status or "Unknown",
                    "met": False
                })

        return {
            "met": met_count,
            "total": total_count,
            "percentage": round((met_count / total_count) * 100, 2) if total_count > 0 else 0,
            "details": details,
        }

    def _assess_quality(self, badge_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Assess quality-related criteria

        Args:
            badge_data: Badge data from API

        Returns:
            Dictionary with quality assessment
        """
        met_count = 0
        total_count = len(self.QUALITY_CRITERIA)
        details = []

        for criterion in self.QUALITY_CRITERIA:
            status = badge_data.get(criterion + "_status")

            if status == "Met":
                met_count += 1
                details.append({
                    "criterion": criterion,
                    "status": "Met",
                    "met": True
                })
            else:
                details.append({
                    "criterion": criterion,
                    "status": status or "Unknown",
                    "met": False
                })

        return {
            "met": met_count,
            "total": total_count,
            "percentage": round((met_count / total_count) * 100, 2) if total_count > 0 else 0,
            "details": details,
        }

    def _extract_criteria_details(self, badge_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract key criteria details from badge data

        Args:
            badge_data: Badge data from API

        Returns:
            Dictionary with key criteria details
        """
        return {
            "name": badge_data.get("name", ""),
            "description": badge_data.get("description", ""),
            "homepage_url": badge_data.get("homepage_url", ""),
            "repo_url": badge_data.get("repo_url", ""),
            "created_at": badge_data.get("created_at", ""),
            "updated_at": badge_data.get("updated_at", ""),
            "badge_percentage": badge_data.get("badge_percentage_0", 0),
            "tiered_percentage": badge_data.get("tiered_percentage", 0),
        }

    def _extract_owner_repo(self, repo_url: str) -> Optional[tuple]:
        """
        Extract owner and repo name from GitHub URL

        Args:
            repo_url: GitHub repository URL

        Returns:
            Tuple of (owner, repo) or None if parsing fails
        """
        # Handle various GitHub URL formats
        patterns = [
            r"github\.com/([^/]+)/([^/]+)",
            r"github\.com:([^/]+)/([^/]+)",
        ]

        for pattern in patterns:
            match = re.search(pattern, repo_url)
            if match:
                owner = match.group(1)
                repo = match.group(2).replace(".git", "")
                return (owner, repo)

        return None

    def _calculate_score(
        self,
        badge_level: str,
        governance: Dict,
        security: Dict,
        quality: Dict
    ) -> Dict[str, Any]:
        """
        Calculate overall OpenSSF Badge score

        Args:
            badge_level: Badge level (passing, silver, gold, in_progress, none)
            governance: Governance assessment
            security: Security assessment
            quality: Quality assessment

        Returns:
            Dictionary with overall score
        """
        # Badge level base score
        level_scores = {
            "gold": 100,
            "silver": 75,
            "passing": 50,
            "in_progress": 25,
            "none": 0
        }

        base_score = level_scores.get(badge_level, 0)

        # Category scores
        governance_pct = governance.get("percentage", 0)
        security_pct = security.get("percentage", 0)
        quality_pct = quality.get("percentage", 0)

        # Weighted average (badge level is primary, categories are secondary)
        if badge_level != "none":
            weighted_score = (
                base_score * 0.5 +
                governance_pct * 0.2 +
                security_pct * 0.15 +
                quality_pct * 0.15
            )
        else:
            weighted_score = 0

        details = [
            f"Badge Level: {badge_level.upper()}",
            f"Governance: {governance_pct:.1f}% ({governance['met']}/{governance['total']})",
            f"Security: {security_pct:.1f}% ({security['met']}/{security['total']})",
            f"Quality: {quality_pct:.1f}% ({quality['met']}/{quality['total']})",
        ]

        return {
            "score": round(weighted_score, 2),
            "max_score": 100,
            "badge_level": badge_level,
            "category_scores": {
                "governance": governance_pct,
                "security": security_pct,
                "quality": quality_pct,
            },
            "details": details,
        }

    def _no_badge_result(self, repo_name: str, repository: str) -> Dict[str, Any]:
        """
        Return result structure when no badge is found

        Args:
            repo_name: Repository name
            repository: Repository owner/repo

        Returns:
            Dictionary with empty badge result
        """
        return {
            "package_name": repo_name,
            "repository": repository,
            "timestamp": self._get_timestamp(),
            "badge_status": {
                "has_badge": False,
                "badge_level": "none",
                "badge_id": None,
                "badge_url": None,
                "percentage": 0,
            },
            "governance_criteria": {
                "met": 0,
                "total": len(self.GOVERNANCE_CRITERIA),
                "percentage": 0,
                "details": [],
            },
            "security_criteria": {
                "met": 0,
                "total": len(self.SECURITY_CRITERIA),
                "percentage": 0,
                "details": [],
            },
            "quality_criteria": {
                "met": 0,
                "total": len(self.QUALITY_CRITERIA),
                "percentage": 0,
                "details": [],
            },
            "criteria_details": {},
            "overall_score": {
                "score": 0,
                "max_score": 100,
                "badge_level": "none",
                "category_scores": {
                    "governance": 0,
                    "security": 0,
                    "quality": 0,
                },
                "details": ["No OpenSSF Badge found"],
            },
        }

    def _empty_result(self, repo_name: str) -> Dict[str, Any]:
        """
        Return empty result structure

        Args:
            repo_name: Repository name

        Returns:
            Dictionary with empty result
        """
        return self._no_badge_result(repo_name, "unknown")

    def _get_timestamp(self) -> str:
        """Get current timestamp in ISO format"""
        from datetime import datetime
        return datetime.utcnow().isoformat() + "Z"


# Example usage
async def main():
    """Example of using OpenSSFBadgeCollector"""

    # Setup logging
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Example packages to test
    test_packages = [
        {
            "name": "curl",
            "repo_url": "https://github.com/curl/curl"
        },
        {
            "name": "openssl",
            "repo_url": "https://github.com/openssl/openssl"
        },
        {
            "name": "hdf5",
            "repo_url": "https://github.com/HDFGroup/hdf5"
        }
    ]

    # Initialize collector
    collector = OpenSSFBadgeCollector()

    # Collect metrics for each package
    results = []
    for package in test_packages:
        result = await collector.collect(package)
        results.append(result)

        # Pretty print results
        print("\n" + "="*70)
        print(f"Package: {result['package_name']}")
        print(f"Repository: {result['repository']}")
        print(f"Badge Level: {result['badge_status']['badge_level']}")
        print(f"Overall Score: {result['overall_score']['score']:.2f}/100")

        if result['badge_status']['has_badge']:
            print(f"Badge URL: {result['badge_status']['badge_url']}")
            print(f"\nGovernance: {result['governance_criteria']['percentage']:.1f}%")
            print(f"Security: {result['security_criteria']['percentage']:.1f}%")
            print(f"Quality: {result['quality_criteria']['percentage']:.1f}%")
        else:
            print("No OpenSSF Badge found for this repository")

        print("="*70)

    # Save results to JSON
    import json
    with open("openssf_badge_results.json", "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nResults saved to openssf_badge_results.json")


if __name__ == "__main__":
    asyncio.run(main())
