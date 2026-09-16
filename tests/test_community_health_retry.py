"""Unit tests for CommunityHealthCollector._github_get retry behavior.

Regression coverage for the bug where a GitHub secondary rate limit during
_list_dir's concurrent root/.github/docs listing silently produced an empty
index -- making kokkos/kokkos's real docs/CODE_OF_CONDUCT.md and
docs/CONTRIBUTING.md read as "not found" even though the pattern list
already covered that path.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from collectors.ecosystem.community_health import CommunityHealthCollector


@pytest.fixture
def collector():
    return CommunityHealthCollector()


def _resp(status_code, json_body=None, headers=None):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = json_body
    r.headers = headers or {}
    return r


def _patched_client(get_mock):
    """Patch httpx.AsyncClient so `async with httpx.AsyncClient(...)` yields a
    client whose .get is get_mock -- _github_get opens a fresh client per call."""
    client = AsyncMock()
    client.get = get_mock
    ctx = AsyncMock()
    ctx.__aenter__.return_value = client
    return patch("collectors.ecosystem.community_health.httpx.AsyncClient", return_value=ctx)


class TestGithubGet:
    def test_success_returns_json(self, collector):
        get_mock = AsyncMock(return_value=_resp(200, [{"name": "docs", "type": "dir"}]))
        with _patched_client(get_mock):
            result = asyncio.run(collector._github_get("https://api.github.com/repos/o/r/contents"))
        assert result == [{"name": "docs", "type": "dir"}]

    def test_403_retries_then_succeeds(self, collector):
        # Exact incident shape: docs/ listing 403s once, then succeeds.
        get_mock = AsyncMock(
            side_effect=[_resp(403), _resp(200, [{"name": "CODE_OF_CONDUCT.md", "type": "file"}])]
        )
        with _patched_client(get_mock):
            result = asyncio.run(collector._github_get("https://api.github.com/repos/kokkos/kokkos/contents/docs"))
        assert result == [{"name": "CODE_OF_CONDUCT.md", "type": "file"}]
        assert get_mock.call_count == 2

    def test_404_returns_none_without_retry(self, collector):
        get_mock = AsyncMock(return_value=_resp(404))
        with _patched_client(get_mock):
            result = asyncio.run(collector._github_get("https://api.github.com/repos/o/r/contents/missing"))
        assert result is None
        assert get_mock.call_count == 1

    def test_403_exhausts_retries_returns_none(self, collector):
        get_mock = AsyncMock(return_value=_resp(403))
        with _patched_client(get_mock):
            result = asyncio.run(collector._github_get("https://api.github.com/repos/o/r"))
        assert result is None
        assert get_mock.call_count == 3


class TestListDirUsesRetry:
    def test_transient_403_does_not_hide_real_files(self, collector):
        """_list_dir must not collapse a throttled request into "empty dir"."""
        get_mock = AsyncMock(
            side_effect=[_resp(403), _resp(200, [{"name": "CODE_OF_CONDUCT.md", "type": "file"}])]
        )
        with _patched_client(get_mock):
            index = asyncio.run(collector._list_dir("kokkos", "kokkos", "docs"))
        assert "docs/code_of_conduct.md" in index
