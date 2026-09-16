"""Shared base class for GitHub-based ecosystem collectors."""

import asyncio
import re
import httpx
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Statuses worth retrying. 429/5xx are unambiguous; 403 is GitHub's shared
# code for both a real permission error and its *secondary* (abuse-detection)
# rate limit, so it needs the extra check below before retrying.
_RETRYABLE_STATUSES = {403, 429, 500, 502, 503}
_RETRY_ATTEMPTS = 2


class RetryingTransport(httpx.AsyncBaseTransport):
    """A single, shared retry policy for every collector's httpx client.

    Wraps the default transport so 403/429/5xx from GitHub's API get retried
    with Retry-After-aware backoff, transparently to whatever code issued the
    request -- no caller needs its own retry loop or even to know this
    exists. This replaced three independent, slightly different hand-rolled
    retry loops (base.py, integrations/github_api.py's PyGithub wrapper, and
    community_health.py) after the same bug -- a GitHub secondary rate limit
    during this pipeline's concurrent per-package collection silently read
    as "no data" instead of being retried -- turned up in three places.

    A plain permission 403 (private repo, bad token) is NOT retried: only a
    403 carrying a Retry-After header is treated as the throttle it actually
    is. This is deliberately narrower than message-sniffing for "secondary
    rate limit"/"abuse" text: a first version did that too, and multiplying
    every throttled call across ~20 collectors x 70+ packages up to 3x each
    pushed total request volume for a full run past GitHub's 5,000/hour
    authenticated quota, which produced a *worse* outcome (near-total data
    loss once the primary quota was exhausted) than the original bug.
    GitHub's own docs recommend keying off Retry-After specifically; requiring
    it here is a stricter, cheaper signal that retries less often, on purpose.
    Returns the final response either way (success, or the last failure once
    retries are exhausted) so a caller's existing
    `if response.status_code != 200` check keeps working completely
    unchanged.
    """

    def __init__(self, wrapped: Optional[httpx.AsyncBaseTransport] = None):
        self._wrapped = wrapped or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response: Optional[httpx.Response] = None
        for attempt in range(_RETRY_ATTEMPTS):
            response = await self._wrapped.handle_async_request(request)
            if response.status_code not in _RETRYABLE_STATUSES:
                return response

            retry_after = response.headers.get("Retry-After")
            if response.status_code == 403 and not retry_after:
                await response.aread()
                return response

            if attempt < _RETRY_ATTEMPTS - 1:
                await response.aread()
                delay = float(retry_after) if retry_after else min(30, 3 * (2 ** attempt))
                logger.debug(
                    f"HTTP {response.status_code} from {request.url}, retrying in {delay:.0f}s"
                )
                await asyncio.sleep(delay)
        return response

    async def aclose(self) -> None:
        await self._wrapped.aclose()


class GitHubCollectorBase:
    """Provides shared GitHub API utilities for ecosystem collectors."""

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
        repo's actual default branch is used (works for develop, main,
        master, or any other default). Retrying a throttled request is the
        client's job now (see RetryingTransport) -- callers just need to
        build their httpx.AsyncClient with transport=RetryingTransport().

        `path` may point at a directory (e.g. ".github/workflows"), in which
        case the Contents API returns a JSON list rather than a dict — handled
        explicitly below since a plain `.get("html_url", ...)` on a list raises
        AttributeError, which previously got swallowed and misreported as
        "not found".
        """
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
        try:
            response = await client.get(url, headers=self.github_headers)
        except Exception as e:
            logger.debug(f"Error checking {path}: {e}")
            return None
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list):
                return f"https://github.com/{owner}/{repo}/tree/HEAD/{path}"
            return data.get("html_url", url)
        return None

    async def _github_get(
        self, client: httpx.AsyncClient, url: str, params: Optional[dict] = None
    ) -> Optional[object]:
        """GET a GitHub API endpoint and return the parsed JSON body.

        Retrying a throttled request is the client's job now (see
        RetryingTransport). Returns None for a real 404, or if the final
        response after retries still isn't a 200; callers should treat None
        as "unknown", not "zero" or "absent", per CASS §3.5.
        """
        try:
            response = await client.get(url, headers=self.github_headers, params=params)
        except Exception as e:
            logger.debug(f"Error fetching {url}: {e}")
            return None
        if response.status_code == 200:
            return response.json()
        return None

    def _get_timestamp(self) -> str:
        """Return current UTC timestamp in ISO format."""
        return datetime.now(timezone.utc).isoformat()
