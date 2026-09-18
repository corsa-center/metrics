"""GitHub-flavored forge: shared GitHub REST API utilities for collectors.

This is collectors/ecosystem/base.py's former `GitHubCollectorBase`, moved
here unchanged (same method names and behavior) as the first step of
splitting platform-specific fetching out of collectors/ecosystem/base.py
and into one forge module per platform (see forge/gitlab.py).
"""

import re
import httpx
import logging
from datetime import datetime, timezone
from typing import Optional

from forge.base import COLLECTION_GAP

logger = logging.getLogger(__name__)


class GitHubForge:
    """Provides shared GitHub API utilities for ecosystem/quality collectors."""

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
    ):
        """Return the file's html_url if it exists, None if confirmed absent
        (a real 404), or COLLECTION_GAP if we couldn't actually tell.

        Using the GitHub Contents API without a ?ref= parameter so the
        repo's actual default branch is used (works for develop, main,
        master, or any other default). Retrying a throttled request is the
        client's job now (see RetryingTransport) -- callers just need to
        build their httpx.AsyncClient with transport=RetryingTransport().

        `path` may point at a directory (e.g. ".github/workflows"), in which
        case the Contents API returns a JSON list rather than a dict — handled
        explicitly below since a plain `.get("html_url", ...)` on a list raises
        AttributeError, which previously got swallowed and misreported as
        "not found".

        COLLECTION_GAP is falsy, same as None, so `if not result:` keeps
        working unchanged for callers that haven't opted into the
        distinction; `if result is COLLECTION_GAP:` is for ones that have.
        """
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
        try:
            response = await client.get(url, headers=self.github_headers)
        except Exception as e:
            # A network-level exception never reaches RetryingTransport's own
            # status-code-based retry/logging (it's raised from inside the
            # wrapped transport, before there's a response to inspect), so
            # this is the only place that sees it -- log it here rather than
            # let it join the same silent "None" every other gap collapses
            # into.
            logger.warning(f"COLLECTION-GAP url={url} status=exception reason={e!r}")
            return COLLECTION_GAP
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list):
                return f"https://github.com/{owner}/{repo}/tree/HEAD/{path}"
            return data.get("html_url", url)
        if response.status_code == 404:
            return None
        return COLLECTION_GAP

    async def _github_get(
        self, client: httpx.AsyncClient, url: str, params: Optional[dict] = None
    ):
        """GET a GitHub API endpoint and return the parsed JSON body, None
        for a confirmed 404, or COLLECTION_GAP if we couldn't actually tell
        (rate limit, network error, other non-2xx). Per CASS §3.5, only a
        confirmed 404 should read as "absent" -- COLLECTION_GAP is falsy,
        same as None, so `if not data:` keeps working unchanged for callers
        that haven't opted into the distinction; `if data is COLLECTION_GAP:`
        is for ones that have.
        """
        try:
            response = await client.get(url, headers=self.github_headers, params=params)
        except Exception as e:
            logger.warning(f"COLLECTION-GAP url={url} status=exception reason={e!r}")
            return COLLECTION_GAP
        if response.status_code == 200:
            return response.json()
        if response.status_code == 404:
            return None
        return COLLECTION_GAP

    def _get_timestamp(self) -> str:
        """Return current UTC timestamp in ISO format."""
        return datetime.now(timezone.utc).isoformat()
