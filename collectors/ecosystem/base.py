"""Shared base class for GitHub-based ecosystem collectors."""

import asyncio
import re
import httpx
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml

logger = logging.getLogger(__name__)

# Statuses worth retrying. 429/5xx are unambiguous; 403 is GitHub's shared
# code for both a real permission error and its *secondary* (abuse-detection)
# rate limit, so it needs the extra check below before retrying.
_RETRYABLE_STATUSES = {403, 429, 500, 502, 503}
_RETRY_ATTEMPTS = 2


class _CollectionGap:
    """Sentinel: this fetch did not succeed, and it was NOT a confirmed 404.

    _check_file_exists/_github_get used to collapse both into the same
    `None` -- a genuine "this file doesn't exist" (trustworthy) and "we
    couldn't tell" (a rate limit, a network error, a non-retried failure)
    read identically to every caller, which is how Kokkos's CHAOSS score
    reached the dashboard as a confident "0.0/100 (critical)" instead of
    "we don't know." Per CASS §3.5, only a confirmed negative should ever
    render as a negative result; anything else should render as not
    collected.

    Deliberately falsy (`bool(COLLECTION_GAP) is False`), so every existing
    `if not data:` / `if data:` check across the codebase keeps working
    exactly as before with zero changes -- this is opt-in. A collector that
    wants to report the distinction checks `is COLLECTION_GAP` explicitly
    and sets not_collected=True instead of a confident False/0.
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
# re-fetched for the same package by at least 5 collectors
# (chaoss_governance.py twice on its own), each treating it as if no one
# else wanted the same thing. Deliberately narrow: only this exact shape is
# cached, not e.g. issues/PRs/releases listings, whose results legitimately
# vary by query params and where staleness risk is less obviously nil.
_REPO_INFO_URL_RE = re.compile(r"^https://api\.github\.com/repos/[^/]+/[^/]+$")


def _clear_repo_info_cache() -> None:
    """Test-only: module-level cache state must not leak between tests."""
    _repo_info_cache.clear()


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


# --------------------------------------------------------------------------- #
# Threshold registry                                                          #
# --------------------------------------------------------------------------- #
# See config/thresholds.yaml for the values themselves and the full design
# rationale. In short: every configurable pass/fail threshold used to be a
# magic number duplicated (or, in three cases, defined but never actually
# read) across collectors and orchestrator.py's rendering code. This module
# is now the one place that resolves a threshold value, whether the call
# site is a collector deciding its own sub_scores or orchestrator.py
# deciding what to render.

_ThresholdValue = Union[int, float, List[str]]

_THRESHOLDS_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "thresholds.yaml"


class ThresholdRegistry:
    """Resolves a (section, label[, param]) key to its configured value.

    Defaults are loaded once from config/thresholds.yaml. Overrides (from
    config/orchestrator.yaml's own `thresholds:` block today; per-project and
    per-package overrides are not wired up yet) are applied on top via
    `set_overrides`, called once at orchestrator startup -- well before any
    collector's `collect()` runs, so every `get()` call during an actual run
    sees the final, merged value.
    """

    def __init__(self, defaults_path: Path):
        self._defaults_path = defaults_path
        self._defaults: Dict[str, Dict[str, Any]] = self._load(defaults_path)
        self._overrides: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def _load(path: Path) -> Dict[str, Dict[str, Any]]:
        if not path.exists():
            return {}
        data = yaml.safe_load(path.read_text()) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{path} must be a mapping of section -> label -> value")
        return data

    def set_overrides(self, overrides: Optional[Dict[str, Dict[str, Any]]]) -> None:
        """Install config-file overrides, validating each key exists first.

        An override for a (section, label) or (section, label, param) that
        isn't already in the defaults is almost always a typo -- the whole
        point of this registry is that a threshold with nobody reading it
        fails immediately, not silently, so this raises rather than storing
        an override nothing will ever look up.
        """
        overrides = overrides or {}
        for section, labels in overrides.items():
            if not isinstance(labels, dict):
                raise ValueError(
                    f"thresholds override for section {section!r} must be a mapping "
                    f"of sub-metric label -> value"
                )
            default_labels = self._defaults.get(section)
            if default_labels is None:
                raise ValueError(
                    f"thresholds override references section {section!r}, which has "
                    f"no defaults in {self._defaults_path} -- not a recognized "
                    f"threshold section"
                )
            for label, value in labels.items():
                if label not in default_labels:
                    raise ValueError(
                        f"thresholds override references {section!r} / {label!r}, "
                        f"which has no default -- not a recognized threshold"
                    )
                default_value = default_labels[label]
                if value is None:
                    raise ValueError(
                        f"thresholds override for {section!r} / {label!r} is null "
                        f"-- omit the key entirely instead of overriding to no value"
                    )
                if isinstance(default_value, dict):
                    if not isinstance(value, dict):
                        raise ValueError(
                            f"thresholds override for {section!r} / {label!r} must "
                            f"be a mapping of parameter name -> value, since the "
                            f"default has named parameters -- got "
                            f"{type(value).__name__}"
                        )
                    unknown = set(value) - set(default_value)
                    if unknown:
                        raise ValueError(
                            f"thresholds override for {section!r} / {label!r} sets "
                            f"unrecognized parameter(s) {sorted(unknown)}"
                        )
                elif isinstance(value, dict):
                    raise ValueError(
                        f"thresholds override for {section!r} / {label!r} is a "
                        f"mapping, but the default is a single value"
                    )
        self._overrides = overrides

    def get(self, section: str, label: str, param: Optional[str] = None) -> _ThresholdValue:
        """Resolve one threshold value, overrides applied.

        Raises KeyError if (section, label[, param]) has no default at
        all -- that's a call site bug (referencing a threshold this
        registry was never told about), not a config problem, so it's not
        caught by set_overrides's validation and fails at the call site
        instead.
        """
        try:
            default_value = self._defaults[section][label]
        except KeyError:
            raise KeyError(
                f"no threshold default for {section!r} / {label!r} -- add one to "
                f"{self._defaults_path} first"
            ) from None

        override_value = self._overrides.get(section, {}).get(label)

        if isinstance(default_value, dict):
            if param is None:
                raise KeyError(
                    f"threshold {section!r} / {label!r} has named parameters -- "
                    f"call with param=<name>, not a bare lookup"
                )
            if param not in default_value:
                raise KeyError(
                    f"no threshold default for {section!r} / {label!r} / {param!r}"
                )
            if isinstance(override_value, dict) and param in override_value:
                return override_value[param]
            return default_value[param]

        if param is not None:
            raise KeyError(
                f"threshold {section!r} / {label!r} has no parameters "
                f"(it's a single value) -- called with param={param!r}"
            )
        if override_value is not None:
            return override_value
        return default_value


_REGISTRY = ThresholdRegistry(_THRESHOLDS_PATH)


def get_threshold(section: str, label: str, param: Optional[str] = None) -> _ThresholdValue:
    """Module-level convenience wrapper around the shared registry instance."""
    return _REGISTRY.get(section, label, param)


def configure_threshold_overrides(overrides: Optional[Dict[str, Dict[str, Any]]]) -> None:
    """Install threshold overrides from config/orchestrator.yaml's `thresholds:`
    block. Call once at startup, before any collector runs."""
    _REGISTRY.set_overrides(overrides)
