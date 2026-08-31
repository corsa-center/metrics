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
    ) -> Optional[str]:
        """Return the file's html_url if it exists, None otherwise.

        Using the GitHub Contents API without a ?ref= parameter so the
        repo's actual default branch is used (works for develop, main, master,
        or any other default).  Retries once on transient errors.

        The return value is truthy when the file exists (non-empty URL string)
        and falsy when it does not (None), so callers using `if result:` work
        without change.  Callers that need the URL can use the returned string.

        `path` may point at a directory (e.g. ".github/workflows"), in which
        case the Contents API returns a JSON list rather than a dict — handled
        explicitly below since a plain `.get("html_url", ...)` on a list raises
        AttributeError, which previously got swallowed and misreported as
        "not found".
        """
        import asyncio
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
        attempts = 3
        for attempt in range(attempts):
            try:
                response = await client.get(url, headers=self.github_headers)
                if response.status_code == 200:
                    data = response.json()
                    if isinstance(data, list):
                        return f"https://github.com/{owner}/{repo}/tree/HEAD/{path}"
                    return data.get("html_url", url)
                if response.status_code == 404:
                    return None
                # 403 is how GitHub signals a *secondary* rate limit — too many
                # concurrent requests — not a permanent denial. Treating it as
                # "file absent" turns throttling into a silent wrong answer.
                if attempt < attempts - 1 and response.status_code in (403, 429, 500, 502, 503):
                    retry_after = response.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after else min(30, 3 * (2 ** attempt))
                    logger.debug(
                        f"HTTP {response.status_code} checking {path}, retrying in {delay:.0f}s"
                    )
                    await asyncio.sleep(delay)
                    continue
                return None
            except Exception as e:
                if attempt < attempts - 1:
                    logger.debug(f"Error checking {path}: {e}, retrying…")
                    await asyncio.sleep(1 + attempt)
                    continue
                return None
        return None

    def _get_timestamp(self) -> str:
        """Return current UTC timestamp in ISO format."""
        return datetime.now(timezone.utc).isoformat()
