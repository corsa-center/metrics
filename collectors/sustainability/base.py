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
        """Return True if the given path exists in the repository.

        Retries once on 429 (rate limit) or 5xx errors to handle transient
        CI failures that previously caused files to be silently missed.
        """
        import asyncio
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
        for attempt in range(2):
            try:
                response = await client.get(url, headers=self.github_headers)
                if response.status_code == 200:
                    return True
                if response.status_code == 404:
                    return False
                # Rate limit or server error — wait and retry once
                if attempt == 0 and response.status_code in (429, 500, 502, 503):
                    logger.warning(f"HTTP {response.status_code} checking {path}, retrying…")
                    await asyncio.sleep(2)
                    continue
                return False
            except Exception as e:
                if attempt == 0:
                    logger.debug(f"Error checking {path}: {e}, retrying…")
                    await asyncio.sleep(1)
                    continue
                return False
        return False

    def _get_timestamp(self) -> str:
        """Return current UTC timestamp in ISO format."""
        return datetime.now(timezone.utc).isoformat()
