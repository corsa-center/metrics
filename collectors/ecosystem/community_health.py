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

    # Vocabulary that indicates a documented decision-making process rather
    # than a document that merely exists.
    GOVERNANCE_KEYWORDS = {
        "Decision process": [
            "consensus", "vote", "voting", "quorum", "majority", "veto",
            "decision-making", "decision making", "rfc",
        ],
        "Defined roles": [
            "maintainer", "committer", "steering", "technical committee", "tsc",
            "core team", "reviewer", "triager", "working group",
        ],
        "Membership lifecycle": [
            "nomination", "nominate", "onboarding", "offboarding", "emeritus",
            "stepping down", "become a maintainer", "promotion",
        ],
        "Conflict resolution": [
            "escalat", "dispute", "conflict resolution", "appeal", "enforcement",
            "code of conduct committee",
        ],
    }

    CODEOWNERS_PATHS = [
        "CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS",
    ]

    # A governance document untouched for this long has stopped describing how
    # the project actually runs.
    GOVERNANCE_STALE_DAYS = 1095  # three years

    # Keyword groups needed before the documented process counts as substantive.
    MIN_KEYWORD_GROUPS = 2

    def __init__(self, github_token: Optional[str] = None):
        """Initialize collector with optional GitHub token"""
        self.github_token = github_token
        self.headers = {"Accept": "application/vnd.github.v3+json"}
        if github_token:
            self.headers["Authorization"] = f"token {github_token}"

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

        # One listing of the directories these documents live in, matched
        # case-insensitively, instead of guessing spellings one request at a time.
        index = await self._build_file_index(owner, repo)
        coc_result = self._match_pattern(index, self.COC_PATTERNS)
        governance_result = self._match_pattern(index, self.GOVERNANCE_PATTERNS)
        contributing_result = self._match_pattern(index, self.CONTRIBUTING_PATTERNS)

        # Get community profile from GitHub API (if token available)
        community_profile = await self._get_community_profile(owner, repo)

        # Section 4.2.2 asks not just whether these documents exist but whether
        # they describe a real process and are still being maintained.
        keyword_analysis = await self._analyze_governance_keywords(
            owner, repo, [governance_result, contributing_result, coc_result]
        )
        effectiveness = await self._assess_effectiveness(
            owner, repo, [governance_result, contributing_result]
        )

        return {
            "package_name": repo_name,
            "repository": f"{owner}/{repo}",
            "timestamp": self._get_timestamp(),
            "code_of_conduct": coc_result,
            "governance": governance_result,
            "contributing_guidelines": contributing_result,
            "community_profile": community_profile,
            "keyword_analysis": keyword_analysis,
            "effectiveness": effectiveness,
            "overall_score": self._calculate_score(
                coc_result, governance_result, contributing_result
            ),
        }

    async def _analyze_governance_keywords(
        self, owner: str, repo: str, documents: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Which decision-making concepts the governance documents actually cover.

        Reads the full documents rather than the 200-character preview kept for
        display — a preview is the title and a sentence, which says nothing about
        whether a decision process is written down.
        """
        paths = [d.get("file_path") for d in documents if d.get("exists") and d.get("file_path")]
        if not paths:
            return {"groups_found": [], "documents_read": 0}

        texts = await asyncio.gather(
            *[self._get_file_text(owner, repo, p) for p in paths],
            return_exceptions=True,
        )
        corpus = " ".join(
            t.lower() for t in texts if isinstance(t, str) and t
        )
        if not corpus:
            return {"groups_found": [], "documents_read": 0}

        found = [
            group for group, terms in self.GOVERNANCE_KEYWORDS.items()
            if any(term in corpus for term in terms)
        ]
        return {"groups_found": found, "documents_read": len(paths)}

    async def _assess_effectiveness(
        self, owner: str, repo: str, documents: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Whether governance is live: owners assigned and documents maintained."""
        paths = [d.get("file_path") for d in documents if d.get("exists") and d.get("file_path")]

        has_codeowners = False
        for path in self.CODEOWNERS_PATHS:
            if (await self._check_file_exists(owner, repo, path)).get("exists"):
                has_codeowners = True
                break

        last_updated_days = None
        if paths:
            ages = await asyncio.gather(
                *[self._days_since_last_change(owner, repo, p) for p in paths],
                return_exceptions=True,
            )
            valid = [a for a in ages if isinstance(a, int)]
            if valid:
                last_updated_days = min(valid)

        maintained = (
            last_updated_days is not None
            and last_updated_days <= self.GOVERNANCE_STALE_DAYS
        )
        return {
            "has_codeowners": has_codeowners,
            "days_since_governance_update": last_updated_days,
            "maintained": maintained,
        }

    async def _get_file_text(self, owner: str, repo: str, path: str) -> str:
        """Full decoded text of a repository file."""
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers=self.headers)
                if resp.status_code != 200:
                    return ""
                import base64
                return base64.b64decode(resp.json().get("content", "")).decode("utf-8", "replace")
        except Exception as e:
            logger.debug(f"Could not read {path}: {e}")
            return ""

    async def _days_since_last_change(
        self, owner: str, repo: str, path: str
    ) -> Optional[int]:
        """Days since the most recent commit touching a given path."""
        url = f"https://api.github.com/repos/{owner}/{repo}/commits"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    url, headers=self.headers, params={"path": path, "per_page": 1}
                )
                if resp.status_code != 200:
                    return None
                data = resp.json()
                if not data:
                    return None
                from datetime import datetime, timezone
                when = data[0]["commit"]["committer"]["date"]
                dt = datetime.fromisoformat(when.replace("Z", "+00:00"))
                return (datetime.now(timezone.utc) - dt).days
        except Exception as e:
            logger.debug(f"Could not date {path}: {e}")
            return None

    async def _list_dir(self, owner: str, repo: str, path: str = "") -> Dict[str, Dict]:
        """Directory listing keyed by lower-cased path, for case-insensitive lookup.

        GitHub's Contents API is case-sensitive, so a pattern list can only ever
        match the spellings someone thought to enumerate. ADIOS2 names its guide
        `Contributing.md`, which no reasonable list of upper/lower variants
        catches, and the file was invisible to this collector.
        """
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}".rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers=self.headers)
                if resp.status_code != 200:
                    return {}
                entries = resp.json()
                if not isinstance(entries, list):
                    return {}
                prefix = f"{path}/" if path else ""
                return {
                    f"{prefix}{e['name']}".lower(): e
                    for e in entries if e.get("type") == "file"
                }
        except Exception as e:
            logger.debug(f"Could not list {path or 'root'}: {e}")
            return {}

    async def _build_file_index(self, owner: str, repo: str) -> Dict[str, Dict]:
        """Case-insensitive index of the directories community docs live in."""
        listings = await asyncio.gather(
            self._list_dir(owner, repo),
            self._list_dir(owner, repo, ".github"),
            self._list_dir(owner, repo, "docs"),
            return_exceptions=True,
        )
        index: Dict[str, Dict] = {}
        for listing in listings:
            if isinstance(listing, dict):
                index.update(listing)
        return index

    def _match_pattern(
        self, index: Dict[str, Dict], patterns: List[str]
    ) -> Dict[str, Any]:
        """First pattern present in the index, compared case-insensitively."""
        for pattern in patterns:
            entry = index.get(pattern.lower())
            if entry:
                return {
                    "exists": True,
                    "file_path": entry.get("path", pattern),
                    "url": entry.get("html_url", ""),
                    "size": entry.get("size", 0),
                    "content_preview": "",
                }
        return {"exists": False, "file_path": None, "url": None}

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
                        "url": data.get("html_url", ""),
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
