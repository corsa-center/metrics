"""
Community Health Metrics Collector

Collects metrics related to community health including:
- Code of Conduct (CoC)
- Governance documentation
- Contributor Guidelines
- Community documentation
"""

import asyncio
import httpx
import logging
from typing import Dict, Any, Optional, List
from pathlib import Path
import re

logger = logging.getLogger(__name__)


class CommunityHealthCollector:
    """Collects community health metrics from GitHub repositories"""

    # Common file patterns for community documents
    COC_PATTERNS = [
        "CODE_OF_CONDUCT.md",
        "CODE_OF_CONDUCT.txt",
        "CODE-OF-CONDUCT.md",
        "code_of_conduct.md",
        "code-of-conduct.md",
        "coc.md",
        "CoC.md",
        "CODE_OF_CONDUCT",
        "docs/CODE_OF_CONDUCT.md",
        ".github/CODE_OF_CONDUCT.md",
    ]

    GOVERNANCE_PATTERNS = [
        "GOVERNANCE.md",
        "GOVERNANCE.txt",
        "governance.md",
        "docs/GOVERNANCE.md",
        "docs/governance.md",
        ".github/GOVERNANCE.md",
        "GOVERNANCE",
        "project-governance.md",
        "PROJECT_GOVERNANCE.md",
    ]

    CONTRIBUTING_PATTERNS = [
        "CONTRIBUTING.md",
        "CONTRIBUTING.txt",
        "contributing.md",
        "CONTRIBUTING",
        "docs/CONTRIBUTING.md",
        "docs/contributing.md",
        ".github/CONTRIBUTING.md",
        "CONTRIBUTE.md",
        "contribute.md",
        "docs/contribute.md",
    ]

    def __init__(self, github_token: Optional[str] = None):
        """Initialize collector with optional GitHub token"""
        self.github_token = github_token
        self.headers = {}
        if github_token:
            self.headers["Authorization"] = f"token {github_token}"
            self.headers["Accept"] = "application/vnd.github.v3+json"

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        """
        Collect community health metrics for a package

        Args:
            package: Dictionary with 'name' and 'repo_url' keys

        Returns:
            Dictionary with community health metrics
        """
        repo_name = package.get("name", "Unknown")
        repo_url = package.get("repo_url", "")

        logger.info(f"Collecting community health metrics for {repo_name}")

        # Extract owner/repo from URL
        owner_repo = self._extract_owner_repo(repo_url)
        if not owner_repo:
            logger.error(f"Could not extract owner/repo from {repo_url}")
            return self._empty_result(repo_name)

        owner, repo = owner_repo

        # Collect metrics
        coc_result = await self._check_code_of_conduct(owner, repo)
        governance_result = await self._check_governance(owner, repo)
        contributing_result = await self._check_contributing(owner, repo)

        # Get community profile from GitHub API (if token available)
        community_profile = await self._get_community_profile(owner, repo)

        return {
            "package_name": repo_name,
            "repository": f"{owner}/{repo}",
            "timestamp": self._get_timestamp(),
            "code_of_conduct": coc_result,
            "governance": governance_result,
            "contributing_guidelines": contributing_result,
            "community_profile": community_profile,
            "overall_score": self._calculate_score(
                coc_result, governance_result, contributing_result
            ),
        }

    async def _check_code_of_conduct(self, owner: str, repo: str) -> Dict[str, Any]:
        """Check for Code of Conduct"""
        logger.info(f"Checking Code of Conduct for {owner}/{repo}")

        for pattern in self.COC_PATTERNS:
            result = await self._check_file_exists(owner, repo, pattern)
            if result["exists"]:
                return {
                    "exists": True,
                    "file_path": pattern,
                    "url": result["url"],
                    "size": result.get("size", 0),
                    "content_preview": result.get("content_preview", ""),
                }

        return {"exists": False, "file_path": None, "url": None}

    async def _check_governance(self, owner: str, repo: str) -> Dict[str, Any]:
        """Check for Governance documentation"""
        logger.info(f"Checking Governance for {owner}/{repo}")

        for pattern in self.GOVERNANCE_PATTERNS:
            result = await self._check_file_exists(owner, repo, pattern)
            if result["exists"]:
                return {
                    "exists": True,
                    "file_path": pattern,
                    "url": result["url"],
                    "size": result.get("size", 0),
                    "content_preview": result.get("content_preview", ""),
                }

        return {"exists": False, "file_path": None, "url": None}

    async def _check_contributing(self, owner: str, repo: str) -> Dict[str, Any]:
        """Check for Contributing guidelines"""
        logger.info(f"Checking Contributing guidelines for {owner}/{repo}")

        for pattern in self.CONTRIBUTING_PATTERNS:
            result = await self._check_file_exists(owner, repo, pattern)
            if result["exists"]:
                return {
                    "exists": True,
                    "file_path": pattern,
                    "url": result["url"],
                    "size": result.get("size", 0),
                    "content_preview": result.get("content_preview", ""),
                }

        return {"exists": False, "file_path": None, "url": None}

    async def _check_file_exists(
        self, owner: str, repo: str, file_path: str
    ) -> Dict[str, Any]:
        """
        Check if a file exists in the repository

        Args:
            owner: Repository owner
            repo: Repository name
            file_path: Path to file to check

        Returns:
            Dictionary with exists, url, size, and optional content_preview
        """
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{file_path}"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=self.headers)
                if response.status_code == 200:
                    data = response.json()

                    # Get content preview (first 200 chars)
                    content_preview = ""
                    if "download_url" in data:
                        preview = await self._get_content_preview(data["download_url"])
                        content_preview = preview

                    return {
                        "exists": True,
                        "url": data.get(
                            "html_url",
                            f"https://github.com/{owner}/{repo}/blob/main/{file_path}",
                        ),
                        "size": data.get("size", 0),
                        "content_preview": content_preview,
                    }
                else:
                    return {"exists": False}
        except Exception as e:
            logger.debug(f"Error checking {file_path}: {e}")
            return {"exists": False}

    async def _get_content_preview(
        self, download_url: str, max_chars: int = 200
    ) -> str:
        """Get preview of file content"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(download_url)
                if response.status_code == 200:
                    text = response.text
                    # Return first 200 chars
                    preview = text[:max_chars].strip()
                    if len(text) > max_chars:
                        preview += "..."
                    return preview
        except Exception as e:
            logger.debug(f"Error getting content preview: {e}")
        return ""

    async def _get_community_profile(self, owner: str, repo: str) -> Dict[str, Any]:
        """
        Get community profile from GitHub API
        This includes health percentage and other community metrics
        """
        if not self.github_token:
            return {}

        url = f"https://api.github.com/repos/{owner}/{repo}/community/profile"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=self.headers)
                if response.status_code == 200:
                    return response.json()
                else:
                    logger.debug(
                        f"Could not get community profile: {response.status_code}"
                    )
                    return {}
        except Exception as e:
            logger.debug(f"Error getting community profile: {e}")
            return {}

    def _extract_owner_repo(self, repo_url: str) -> Optional[tuple]:
        """Extract owner and repo name from GitHub URL"""
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
        self, coc: Dict, governance: Dict, contributing: Dict
    ) -> Dict[str, Any]:
        """Calculate overall community health score"""
        score = 0
        max_score = 3
        details = []

        if coc.get("exists"):
            score += 1
            details.append("Code of Conduct: ✓")
        else:
            details.append("Code of Conduct: ✗")

        if governance.get("exists"):
            score += 1
            details.append("Governance: ✓")
        else:
            details.append("Governance: ✗")

        if contributing.get("exists"):
            score += 1
            details.append("Contributing Guidelines: ✓")
        else:
            details.append("Contributing Guidelines: ✗")

        return {
            "score": score,
            "max_score": max_score,
            "percentage": round((score / max_score) * 100, 2),
            "details": details,
        }

    def _empty_result(self, repo_name: str) -> Dict[str, Any]:
        """Return empty result structure"""
        return {
            "package_name": repo_name,
            "repository": "unknown",
            "timestamp": self._get_timestamp(),
            "code_of_conduct": {"exists": False},
            "governance": {"exists": False},
            "contributing_guidelines": {"exists": False},
            "community_profile": {},
            "overall_score": {
                "score": 0,
                "max_score": 3,
                "percentage": 0,
                "details": [],
            },
        }

    def _get_timestamp(self) -> str:
        """Get current timestamp"""
        from datetime import datetime

        return datetime.utcnow().isoformat() + "Z"
