"""Shared base class for GitHub-based sustainability collectors."""

import re
import httpx
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class GitHubCollectorBase:
    """Provides shared GitHub API utilities for sustainability collectors."""

    def __init__(self, github_token: Optional[str] = None):
        if github_token:
            self.github_headers = {
                "Authorization": f"token {github_token}",
                "Accept": "application/vnd.github.v3+json",
            }
        else:
            self.github_headers = {"Accept": "application/vnd.github.v3+json"}

    def _extract_owner_repo(self, repo_url: str) -> Optional[tuple]:
        """Extract (owner, repo) from a GitHub URL."""
        patterns = [
            r"github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$",
            r"github\.com:([^/]+)/([^/]+?)(?:\.git)?/?$",
        ]
        for pattern in patterns:
            match = re.search(pattern, repo_url)
            if match:
                return (match.group(1), match.group(2).replace(".git", ""))
        return None

    async def _check_file_exists(
        self, client: httpx.AsyncClient, owner: str, repo: str, path: str
    ) -> bool:
        """Return True if the given path exists in the repository."""
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
        try:
            response = await client.get(url, headers=self.github_headers)
            return response.status_code == 200
        except Exception:
            return False

    def _get_timestamp(self) -> str:
        """Return current UTC timestamp in ISO format."""
        return datetime.now(timezone.utc).isoformat()
