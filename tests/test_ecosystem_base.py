"""Unit tests for GitHubCollectorBase._github_get retry behavior.

Regression coverage for the bug where a GitHub secondary rate limit (403,
often with no Retry-After header) during a large concurrent collection run
was silently treated as "no data" instead of retried -- this is what caused
stars/forks/CHAOSS metrics to read as zero for the majority of tracked
packages instead of failing loudly or actually succeeding on retry.
"""

import asyncio
import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock

from collectors.ecosystem.base import GitHubCollectorBase


@pytest.fixture
def collector():
    return GitHubCollectorBase()


def _resp(status_code, json_body=None, headers=None):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = json_body
    r.headers = headers or {}
    return r


class TestGithubGet:
    def test_success_returns_json(self, collector):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(200, {"stargazers_count": 42}))
        result = asyncio.run(collector._github_get(client, "https://api.github.com/repos/o/r"))
        assert result == {"stargazers_count": 42}

    def test_404_returns_none_without_retry(self, collector):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(404))
        result = asyncio.run(collector._github_get(client, "https://api.github.com/repos/o/r"))
        assert result is None
        assert client.get.call_count == 1

    def test_403_retries_then_succeeds(self, collector):
        # This is the exact failure mode from the live incident: a secondary
        # rate limit 403 on the first attempt, real data on the next.
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[_resp(403), _resp(200, {"stargazers_count": 2687})]
        )
        result = asyncio.run(collector._github_get(client, "https://api.github.com/repos/kokkos/kokkos"))
        assert result == {"stargazers_count": 2687}
        assert client.get.call_count == 2

    def test_403_exhausts_retries_returns_none(self, collector):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(403))
        result = asyncio.run(collector._github_get(client, "https://api.github.com/repos/o/r"))
        assert result is None
        assert client.get.call_count == 3

    def test_429_honors_retry_after_header(self, collector):
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[_resp(429, headers={"Retry-After": "0"}), _resp(200, {"ok": True})]
        )
        result = asyncio.run(collector._github_get(client, "https://api.github.com/repos/o/r"))
        assert result == {"ok": True}

    def test_transient_exception_retries_then_succeeds(self, collector):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=[httpx.ConnectError("boom"), _resp(200, {"ok": True})])
        result = asyncio.run(collector._github_get(client, "https://api.github.com/repos/o/r"))
        assert result == {"ok": True}

    def test_params_forwarded(self, collector):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(200, []))
        asyncio.run(
            collector._github_get(
                client, "https://api.github.com/repos/o/r/issues", params={"state": "open"}
            )
        )
        _, kwargs = client.get.call_args
        assert kwargs["params"] == {"state": "open"}
