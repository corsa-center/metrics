"""
OpenSSF Best Practices Badge Collector

This collector:
1. Checks if an OpenSSF Badge exists for the repository
2. If badge exists: retrieves badge level and progress percentage
3. If badge doesn't exist: scans repository to assess missing requirements
4. Outputs detailed metrics on governance, security, and quality criteria

OpenSSF Best Practices Badge API: https://bestpractices.coreinfrastructure.org/projects.json
"""

import asyncio
import httpx
import logging
from typing import Dict, Any, Optional, List
import re

logger = logging.getLogger(__name__)


class OpenSSFBadgeCollector:
    """
    Collects OpenSSF Best Practices Badge metrics

    Flow:
    1. Check if badge exists via API
    2. If yes: get badge level, percentage, and criteria status
    3. If no: scan repository to identify missing requirements
    """

    # OpenSSF Badge API endpoints
    BADGE_API_BASE = "https://bestpractices.coreinfrastructure.org"
    BADGE_SEARCH_URL = f"{BADGE_API_BASE}/projects.json"

    # File patterns to check in repository (if no badge)
    GOVERNANCE_FILES = {
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

    SECURITY_FILES = {
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

    QUALITY_FILES = {
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
            self.github_headers = {
                "Accept": "application/vnd.github.v3+json"
            }

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

        # Step 1: Check if badge exists
        logger.info(f"Step 1: Checking for existing OpenSSF Badge...")
        badge_data = await self._search_badge(owner, repo, repo_url)

        if badge_data:
            # Badge exists - return badge-based metrics
            logger.info(f"✓ Badge found! Level: {badge_data.get('badge_level', 'unknown')}, Progress: {badge_data.get('badge_percentage_0', 0)}%")
            return await self._collect_with_badge(repo_name, owner, repo, badge_data)
        else:
            # No badge - scan repository for missing requirements
            logger.info(f"✗ No badge found. Scanning repository for missing requirements...")
            return await self._collect_without_badge(repo_name, owner, repo)

    async def _collect_with_badge(
        self, repo_name: str, owner: str, repo: str, badge_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Collect metrics when badge exists

        Args:
            repo_name: Package name
            owner: Repository owner
            repo: Repository name
            badge_data: Badge data from API

        Returns:
            Dictionary with badge metrics
        """
        badge_level = self._get_badge_level(badge_data)
        badge_percentage = badge_data.get("badge_percentage_0", 0)
        badge_id = badge_data.get("id")

        # Check if badge is in progress
        is_in_progress = badge_level == "in_progress" or (badge_percentage > 0 and badge_percentage < 100)

        # Assess criteria from badge data
        governance = self._assess_governance_from_badge(badge_data)
        security = self._assess_security_from_badge(badge_data)
        quality = self._assess_quality_from_badge(badge_data)

        # Calculate overall completion
        overall_percentage = badge_percentage

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
            "governance_criteria": governance,
            "security_criteria": security,
            "quality_criteria": quality,
            "overall_score": {
                "score": round(overall_percentage, 2),
                "max_score": 100,
                "percentage": round(overall_percentage, 2),
                "status": "passing" if badge_percentage >= 100 else "in_progress" if badge_percentage > 0 else "not_started",
            },
            "assessment_method": "openssf_badge_api",
        }

    async def _collect_without_badge(
        self, repo_name: str, owner: str, repo: str
    ) -> Dict[str, Any]:
        """
        Collect metrics when no badge exists by scanning repository

        Args:
            repo_name: Package name
            owner: Repository owner
            repo: Repository name

        Returns:
            Dictionary with repository scan metrics
        """
        logger.info(f"Step 2: Scanning repository {owner}/{repo} for OpenSSF requirements...")

        # Scan repository for requirements
        governance_results = await self._scan_governance_files(owner, repo)
        security_results = await self._scan_security_files(owner, repo)
        quality_results = await self._scan_quality_indicators(owner, repo)

        # Calculate what's missing
        governance_pct = self._calculate_percentage(governance_results)
        security_pct = self._calculate_percentage(security_results)
        quality_pct = self._calculate_percentage(quality_results)

        # Overall estimate (weighted average)
        overall_pct = (governance_pct * 0.4) + (security_pct * 0.3) + (quality_pct * 0.3)

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
            "governance_criteria": governance_results,
            "security_criteria": security_results,
            "quality_criteria": quality_results,
            "overall_score": {
                "score": round(overall_pct, 2),
                "max_score": 100,
                "percentage": round(overall_pct, 2),
                "status": "not_started",
                "estimated": True,
            },
            "assessment_method": "repository_scan",
            "recommendation": f"Start OpenSSF Badge at: https://www.bestpractices.dev/projects/new",
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
                        return results[0]

                # Try alternative searches
                search_queries = [
                    f"github.com/{owner}/{repo}",
                    f"{owner}/{repo}",
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
                        for result in results:
                            result_url = result.get("repo_url", "")
                            if f"{owner}/{repo}" in result_url.lower():
                                return result

                return None

        except Exception as e:
            logger.debug(f"Error searching for badge: {e}")
            return None

    async def _scan_governance_files(self, owner: str, repo: str) -> Dict[str, Any]:
        """Scan repository for governance-related files"""
        results = {
            "found": [],
            "missing": [],
            "details": {}
        }

        for criterion, patterns in self.GOVERNANCE_FILES.items():
            found = False
            for pattern in patterns:
                if await self._check_file_exists(owner, repo, pattern):
                    results["found"].append(criterion)
                    results["details"][criterion] = {
                        "exists": True,
                        "file": pattern,
                        "url": f"https://github.com/{owner}/{repo}/blob/main/{pattern}"
                    }
                    found = True
                    logger.info(f"  ✓ {criterion}: {pattern}")
                    break

            if not found:
                results["missing"].append(criterion)
                results["details"][criterion] = {
                    "exists": False,
                    "recommended": patterns[0]
                }
                logger.info(f"  ✗ {criterion}: missing")

        results["count_found"] = len(results["found"])
        results["count_total"] = len(self.GOVERNANCE_FILES)
        results["percentage"] = round((results["count_found"] / results["count_total"]) * 100, 2)

        return results

    async def _scan_security_files(self, owner: str, repo: str) -> Dict[str, Any]:
        """Scan repository for security-related files"""
        results = {
            "found": [],
            "missing": [],
            "details": {}
        }

        for criterion, patterns in self.SECURITY_FILES.items():
            found = False
            for pattern in patterns:
                if await self._check_file_exists(owner, repo, pattern):
                    results["found"].append(criterion)
                    results["details"][criterion] = {
                        "exists": True,
                        "file": pattern,
                        "url": f"https://github.com/{owner}/{repo}/blob/main/{pattern}"
                    }
                    found = True
                    logger.info(f"  ✓ {criterion}: {pattern}")
                    break

            if not found:
                results["missing"].append(criterion)
                results["details"][criterion] = {
                    "exists": False,
                    "recommended": patterns[0]
                }
                logger.info(f"  ✗ {criterion}: missing")

        results["count_found"] = len(results["found"])
        results["count_total"] = len(self.SECURITY_FILES)
        results["percentage"] = round((results["count_found"] / results["count_total"]) * 100, 2)

        return results

    async def _scan_quality_indicators(self, owner: str, repo: str) -> Dict[str, Any]:
        """Scan repository for quality indicators (CI/CD, docs, tests)"""
        results = {
            "found": [],
            "missing": [],
            "details": {}
        }

        for criterion, patterns in self.QUALITY_FILES.items():
            found = False
            for pattern in patterns:
                if await self._check_file_exists(owner, repo, pattern):
                    results["found"].append(criterion)
                    results["details"][criterion] = {
                        "exists": True,
                        "file": pattern,
                        "url": f"https://github.com/{owner}/{repo}/blob/main/{pattern}" if not pattern.endswith('/') else f"https://github.com/{owner}/{repo}/tree/main/{pattern}"
                    }
                    found = True
                    logger.info(f"  ✓ {criterion}: {pattern}")
                    break

            if not found:
                results["missing"].append(criterion)
                results["details"][criterion] = {
                    "exists": False,
                    "recommended": patterns[0]
                }
                logger.info(f"  ✗ {criterion}: missing")

        results["count_found"] = len(results["found"])
        results["count_total"] = len(self.QUALITY_FILES)
        results["percentage"] = round((results["count_found"] / results["count_total"]) * 100, 2)

        return results

    async def _check_file_exists(self, owner: str, repo: str, path: str) -> bool:
        """
        Check if a file/directory exists in the repository

        Args:
            owner: Repository owner
            repo: Repository name
            path: File or directory path

        Returns:
            True if exists, False otherwise
        """
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, headers=self.github_headers)
                return response.status_code == 200
        except Exception:
            return False

    def _assess_governance_from_badge(self, badge_data: Dict[str, Any]) -> Dict[str, Any]:
        """Assess governance criteria from badge data"""
        criteria = [
            "governance",
            "contribution",
            "contribution_requirements",
            "code_of_conduct",
        ]

        met = []
        unmet = []
        details = {}

        for criterion in criteria:
            status = badge_data.get(f"{criterion}_status", "Unknown")
            is_met = status == "Met"

            if is_met:
                met.append(criterion)
            else:
                unmet.append(criterion)

            details[criterion] = {
                "status": status,
                "met": is_met
            }

        return {
            "found": met,
            "missing": unmet,
            "details": details,
            "count_found": len(met),
            "count_total": len(criteria),
            "percentage": round((len(met) / len(criteria)) * 100, 2) if criteria else 0,
        }

    def _assess_security_from_badge(self, badge_data: Dict[str, Any]) -> Dict[str, Any]:
        """Assess security criteria from badge data"""
        criteria = [
            "vulnerability_report_process",
            "vulnerability_report_private",
            "security_policy",
        ]

        met = []
        unmet = []
        details = {}

        for criterion in criteria:
            status = badge_data.get(f"{criterion}_status", "Unknown")
            is_met = status == "Met"

            if is_met:
                met.append(criterion)
            else:
                unmet.append(criterion)

            details[criterion] = {
                "status": status,
                "met": is_met
            }

        return {
            "found": met,
            "missing": unmet,
            "details": details,
            "count_found": len(met),
            "count_total": len(criteria),
            "percentage": round((len(met) / len(criteria)) * 100, 2) if criteria else 0,
        }

    def _assess_quality_from_badge(self, badge_data: Dict[str, Any]) -> Dict[str, Any]:
        """Assess quality criteria from badge data"""
        criteria = [
            "test",
            "test_continuous_integration",
            "documentation_basics",
        ]

        met = []
        unmet = []
        details = {}

        for criterion in criteria:
            status = badge_data.get(f"{criterion}_status", "Unknown")
            is_met = status == "Met"

            if is_met:
                met.append(criterion)
            else:
                unmet.append(criterion)

            details[criterion] = {
                "status": status,
                "met": is_met
            }

        return {
            "found": met,
            "missing": unmet,
            "details": details,
            "count_found": len(met),
            "count_total": len(criteria),
            "percentage": round((len(met) / len(criteria)) * 100, 2) if criteria else 0,
        }

    def _calculate_percentage(self, results: Dict[str, Any]) -> float:
        """Calculate percentage from scan results"""
        return results.get("percentage", 0)

    def _get_badge_level(self, badge_data: Dict[str, Any]) -> str:
        """
        Determine badge level from badge data

        Args:
            badge_data: Badge data from API

        Returns:
            Badge level: "passing", "silver", "gold", "in_progress", or "none"
        """
        badge_level_num = badge_data.get("badge_level")

        if badge_level_num == "2":
            return "gold"
        elif badge_level_num == "1":
            return "silver"
        elif badge_level_num == "0":
            return "passing"
        else:
            # Check percentage to determine if in progress
            percentage = badge_data.get("badge_percentage_0", 0)
            if percentage > 0:
                return "in_progress"
            return "none"

    def _extract_owner_repo(self, repo_url: str) -> Optional[tuple]:
        """
        Extract owner and repo name from GitHub URL

        Args:
            repo_url: GitHub repository URL

        Returns:
            Tuple of (owner, repo) or None if parsing fails
        """
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

    def _empty_result(self, repo_name: str) -> Dict[str, Any]:
        """Return empty result structure"""
        return {
            "package_name": repo_name,
            "repository": "unknown",
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
            "governance_criteria": {"found": [], "missing": [], "percentage": 0},
            "security_criteria": {"found": [], "missing": [], "percentage": 0},
            "quality_criteria": {"found": [], "missing": [], "percentage": 0},
            "overall_score": {
                "score": 0,
                "max_score": 100,
                "percentage": 0,
                "status": "error",
            },
            "assessment_method": "error",
        }

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
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    # Example packages to test
    test_packages = [
        {
            "name": "HDF5",
            "repo_url": "https://github.com/HDFGroup/hdf5"
        },
        {
            "name": "curl",
            "repo_url": "https://github.com/curl/curl"
        },
        {
            "name": "numpy",
            "repo_url": "https://github.com/numpy/numpy"
        }
    ]

    # Initialize collector
    import os
    github_token = os.environ.get("GITHUB_TOKEN")
    collector = OpenSSFBadgeCollector(github_token=github_token)

    # Collect metrics for each package
    results = []
    for package in test_packages:
        print("\n" + "="*70)
        result = await collector.collect(package)
        results.append(result)

        # Pretty print results
        print(f"Package: {result['package_name']}")
        print(f"Repository: {result['repository']}")
        print(f"Badge Exists: {result['badge_exists']}")

        if result['badge_exists']:
            print(f"Badge Level: {result['badge_status']['level']}")
            print(f"Progress: {result['badge_status']['progress_percentage']}%")
            print(f"In Progress: {result['badge_status']['in_progress']}")
            print(f"Badge URL: {result['badge_status']['url']}")
        else:
            print("No badge found - repository scanned for requirements")

        print(f"\nGovernance: {result['governance_criteria']['percentage']:.1f}% ({result['governance_criteria']['count_found']}/{result['governance_criteria']['count_total']})")
        print(f"Security: {result['security_criteria']['percentage']:.1f}% ({result['security_criteria']['count_found']}/{result['security_criteria']['count_total']})")
        print(f"Quality: {result['quality_criteria']['percentage']:.1f}% ({result['quality_criteria']['count_found']}/{result['quality_criteria']['count_total']})")
        print(f"\nOverall Score: {result['overall_score']['score']:.2f}/100")
        print(f"Assessment Method: {result['assessment_method']}")
        print("="*70)

    # Save results to JSON
    import json
    with open("4.2.1-openssf_badge.json", "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n4.2.1-openssf_badge.json")


if __name__ == "__main__":
    asyncio.run(main())
