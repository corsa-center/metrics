"""Platform-agnostic HTTP plumbing shared by every forge (GitHub, GitLab, ...).

Nothing in this module knows which code-hosting platform it's talking to --
that's the whole point. `RetryingTransport`'s retry policy and the
COLLECTION_GAP sentinel apply the same way regardless of which forge module
built the request.
"""

import asyncio
import re
import httpx
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Statuses worth retrying. 429/5xx are unambiguous; 403 is GitHub's shared
# code for both a real permission error and its *secondary* (abuse-detection)
# rate limit, so it needs the extra check below before retrying.
_RETRYABLE_STATUSES = {403, 429, 500, 502, 503}
_RETRY_ATTEMPTS = 2


class _CollectionGap:
    """Sentinel: this fetch did not succeed, and it was NOT a confirmed 404.

    A genuine "this resource doesn't exist" (trustworthy) and "we couldn't
    tell" (a rate limit, a network error, a non-retried failure, or a
    capability the platform doesn't expose) must never read identically to
    a caller -- that's how Kokkos's CHAOSS score once reached the dashboard
    as a confident "0.0/100 (critical)" instead of "we don't know." Per CASS
    §3.5, only a confirmed negative should ever render as a negative result;
    anything else should render as not collected.

    Deliberately falsy (`bool(COLLECTION_GAP) is False`), so every existing
    `if not data:` / `if data:` check keeps working exactly as before with
    zero changes -- this is opt-in. A caller that wants to report the
    distinction checks `is COLLECTION_GAP` explicitly and sets
    not_collected=True instead of a confident False/0.
    """

    def __repr__(self) -> str:
        return "<COLLECTION_GAP>"

    def __bool__(self) -> bool:
        return False


COLLECTION_GAP = _CollectionGap()

# Every collector builds its own httpx client, so this has to be module-level
# (not per-instance) to actually coalesce requests issued by different
# collector objects for the same package. One process per orchestrator run,
# so it's never cleared -- at most ~1 entry per tracked package, trivial
# memory, and each URL is only ever fetched during that package's brief
# collection window anyway.
_repo_info_cache: Dict[str, "asyncio.Future[httpx.Response]"] = {}

# The bare repo-info endpoint, no query string -- confirmed independently
# re-fetched for the same package by at least 5 collectors, each treating it
# as if no one else wanted the same thing. Deliberately narrow: only this
# exact shape is cached, not e.g. issues/PRs/releases listings, whose
# results legitimately vary by query params and where staleness risk is
# less obviously nil. GitHub's repo-info shape today; GitLab's
# `/projects/{id}` (no further path segments) matches too once GitLabForge
# starts issuing requests through this transport.
_REPO_INFO_URL_RE = re.compile(
    r"^https://api\.github\.com/repos/[^/]+/[^/]+$"
    r"|^https://[^/]+/api/v4/projects/[^/]+$"
)


def _clear_repo_info_cache() -> None:
    """Test-only: module-level cache state must not leak between tests."""
    _repo_info_cache.clear()


class RetryingTransport(httpx.AsyncBaseTransport):
    """A single, shared retry policy for every forge's httpx client.

    Wraps the default transport so 403/429/5xx get retried with
    Retry-After-aware backoff, transparently to whatever code issued the
    request -- no caller needs its own retry loop or even to know this
    exists. This replaced three independent, slightly different hand-rolled
    retry loops after the same bug -- a GitHub secondary rate limit during
    this pipeline's concurrent per-package collection silently read as "no
    data" instead of being retried -- turned up in three places.

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
    GitLab's 403 has no equivalent secondary-throttle meaning (a GitLab 403
    is always a real permission error), so this carve-out is a no-op there --
    a GitLab 403 simply never carries Retry-After and falls through to the
    "not retried" branch below, which is the correct behavior for it too.
    Returns the final response either way (success, or the last failure once
    retries are exhausted) so a caller's existing
    `if response.status_code != 200` check keeps working completely
    unchanged.
    """

    def __init__(self, wrapped: Optional[httpx.AsyncBaseTransport] = None):
        self._wrapped = wrapped or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and _REPO_INFO_URL_RE.match(str(request.url)):
            return await self._deduped(request)
        return await self._request_with_retry(request)

    async def _deduped(self, request: httpx.Request) -> httpx.Response:
        """Coalesce concurrent/repeated fetches of the same repo-info URL.

        Collectors within one package's collection window fire together via
        asyncio.gather, so a plain "check cache, else fetch" dict would still
        miss on every one of them -- none has finished by the time the next
        one checks. Storing the in-flight Future itself (not just its
        eventual result) means every caller for the same URL awaits the one
        real request in progress instead of starting their own.
        """
        key = str(request.url)
        future = _repo_info_cache.get(key)
        if future is None:
            future = asyncio.ensure_future(self._request_with_retry(request))
            _repo_info_cache[key] = future
        return await future

    async def _request_with_retry(self, request: httpx.Request) -> httpx.Response:
        response: Optional[httpx.Response] = None
        for attempt in range(_RETRY_ATTEMPTS):
            response = await self._wrapped.handle_async_request(request)
            if response.status_code not in _RETRYABLE_STATUSES:
                await response.aread()
                return response

            retry_after = response.headers.get("Retry-After")
            if response.status_code == 403 and not retry_after:
                await response.aread()
                # COLLECTION-GAP: grep-able tag so "why is this metric
                # empty" can be answered from orchestrator.log instead of
                # reproducing the collector locally by hand, which is how
                # every gap in the 2026-09-16 incident actually got
                # diagnosed. Not a 404 (that's a trustworthy "confirmed
                # absent", not a gap) -- this is specifically a 403 with no
                # Retry-After, i.e. a permission error or primary quota
                # exhaustion, neither of which retrying would have fixed.
                logger.warning(
                    f"COLLECTION-GAP url={request.url} status=403 "
                    f"reason=not_retried_no_retry_after"
                )
                return response

            if attempt < _RETRY_ATTEMPTS - 1:
                await response.aread()
                delay = float(retry_after) if retry_after else min(30, 3 * (2 ** attempt))
                logger.debug(
                    f"HTTP {response.status_code} from {request.url}, retrying in {delay:.0f}s"
                )
                await asyncio.sleep(delay)
            else:
                await response.aread()
                logger.warning(
                    f"COLLECTION-GAP url={request.url} status={response.status_code} "
                    f"reason=retries_exhausted attempts={_RETRY_ATTEMPTS}"
                )
        return response

    async def aclose(self) -> None:
        await self._wrapped.aclose()


# A tag naming a version (v5.0.11, name-7-2-0, checkpoint.1.14.0), and the
# pre-release suffixes that shouldn't count as a release on their own.
_VERSION_TAG = re.compile(r"\d+[._-]\d+")
# "<consumer>-YYYY-MM-DD" marks a snapshot known to work with another project
# (e.g. "downstream-2026-03-21"), not a release.
_SNAPSHOT_TAG = re.compile(r"^(?!release)[a-z][\w.]*[-_]\d{4}-\d{2}-\d{2}$", re.I)
_PRE_RELEASE_TAG = re.compile(
    r"(?<![a-z])(?:rc|alpha|beta|pre|dev)(?:[._-]?\d+)?(?![a-z])|\d(?:a|b)\d+", re.I)


def is_release_tag(name: str) -> bool:
    """Whether a tag name marks a real release: a version number that isn't
    a pre-release or a dated compatibility snapshot. Shared by every forge's
    version_tags() so GitHub and GitLab count releases the same way."""
    return bool(
        _VERSION_TAG.search(name)
        and not _PRE_RELEASE_TAG.search(name)
        and not _SNAPSHOT_TAG.search(name)
    )
