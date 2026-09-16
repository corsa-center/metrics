"""Unit tests for CommunityHealthCollector._github_get.

Retry policy for GitHub secondary rate limits lives in RetryingTransport now
(tested in test_ecosystem_base.py) -- these tests just confirm this
collector wires that transport into its httpx client and interprets a
single response correctly, which is all _github_get is responsible for
once retrying isn't its job anymore.

Background: a secondary rate limit during _list_dir's concurrent
root/.github/docs listing used to silently produce an empty index, making
kokkos/kokkos's real docs/CODE_OF_CONDUCT.md and docs/CONTRIBUTING.md read
as "not found" even though the pattern list already covered that path.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from collectors.ecosystem.base import COLLECTION_GAP, RetryingTransport
from collectors.ecosystem.community_health import CommunityHealthCollector


@pytest.fixture
def collector():
    return CommunityHealthCollector()


def _resp(status_code, json_body=None):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = json_body
    return r


def _patched_client(get_mock):
    """Patch httpx.AsyncClient so `async with httpx.AsyncClient(...)` yields a
    client whose .get is get_mock -- _github_get opens a fresh client per call."""
    from unittest.mock import patch

    client = AsyncMock()
    client.get = get_mock
    ctx = AsyncMock()
    ctx.__aenter__.return_value = client
    captured = {}

    def _client_factory(*args, **kwargs):
        captured["kwargs"] = kwargs
        return ctx

    return patch(
        "collectors.ecosystem.community_health.httpx.AsyncClient", side_effect=_client_factory
    ), captured


class TestGithubGet:
    def test_success_returns_json(self, collector):
        get_mock = AsyncMock(return_value=_resp(200, [{"name": "docs", "type": "dir"}]))
        patcher, _ = _patched_client(get_mock)
        with patcher:
            result = asyncio.run(collector._github_get("https://api.github.com/repos/o/r/contents"))
        assert result == [{"name": "docs", "type": "dir"}]

    def test_404_returns_none(self, collector):
        get_mock = AsyncMock(return_value=_resp(404))
        patcher, _ = _patched_client(get_mock)
        with patcher:
            result = asyncio.run(collector._github_get("https://api.github.com/repos/o/r/contents/missing"))
        assert result is None

    def test_non_200_is_a_gap_not_a_negative(self, collector):
        # By the time this code sees the response, RetryingTransport has
        # already retried and given up. Must not read the same as a real
        # 404 -- that's how a rate-limited fetch used to silently score as
        # a confirmed "not found" instead of "unknown".
        get_mock = AsyncMock(return_value=_resp(403))
        patcher, _ = _patched_client(get_mock)
        with patcher:
            result = asyncio.run(collector._github_get("https://api.github.com/repos/o/r"))
        assert result is COLLECTION_GAP

    def test_wires_in_the_retrying_transport(self, collector):
        """The actual fix: without this, _github_get has no retry at all."""
        get_mock = AsyncMock(return_value=_resp(200, {}))
        patcher, captured = _patched_client(get_mock)
        with patcher:
            asyncio.run(collector._github_get("https://api.github.com/repos/o/r"))
        assert isinstance(captured["kwargs"].get("transport"), RetryingTransport)
