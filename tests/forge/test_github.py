"""Unit tests for GitHubForge's file/JSON fetch helpers.

With retries owned by RetryingTransport (see tests/forge/test_base.py),
these only need to prove _check_file_exists/_github_get interpret a single
response correctly.
"""

import asyncio
import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock

from forge.base import COLLECTION_GAP
from forge.github import GitHubForge


@pytest.fixture
def forge():
    return GitHubForge()


def _resp(status_code, json_body=None, headers=None, text=""):
    r = MagicMock(spec=httpx.Response)
    r.status_code = status_code
    r.json.return_value = json_body
    r.headers = httpx.Headers(headers or {})
    r.text = text
    r.aread = AsyncMock()
    return r


class TestCheckFileExists:
    def _client(self, status_code, json_body=None):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(status_code, json_body))
        return client

    def test_file_found(self, forge):
        client = self._client(200, {"html_url": "https://github.com/o/r/blob/main/x"})
        result = asyncio.run(forge._check_file_exists(client, "o", "r", "x"))
        assert result == "https://github.com/o/r/blob/main/x"

    def test_directory_found(self, forge):
        client = self._client(200, [{"name": "a"}, {"name": "b"}])
        result = asyncio.run(forge._check_file_exists(client, "o", "r", "dir"))
        assert result == "https://github.com/o/r/tree/HEAD/dir"

    def test_not_found(self, forge):
        client = self._client(404)
        result = asyncio.run(forge._check_file_exists(client, "o", "r", "x"))
        assert result is None

    def test_final_failure_after_transport_retries_is_a_gap_not_a_negative(self, forge):
        # By the time this code sees the response, the transport has already
        # retried and given up. A 403 here is unknown, not "confirmed
        # absent" -- must not collapse into the same None a real 404 returns.
        client = self._client(403)
        result = asyncio.run(forge._check_file_exists(client, "o", "r", "x"))
        assert result is COLLECTION_GAP
        assert not result  # still falsy, so `if not result:` callers are unaffected

    def test_network_exception_is_a_gap_not_a_negative(self, forge):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.ConnectError("boom"))
        result = asyncio.run(forge._check_file_exists(client, "o", "r", "x"))
        assert result is COLLECTION_GAP


class TestGithubGet:
    def test_success_returns_json(self, forge):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(200, {"stargazers_count": 42}))
        result = asyncio.run(forge._github_get(client, "https://api.github.com/repos/o/r"))
        assert result == {"stargazers_count": 42}

    def test_404_returns_none(self, forge):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(404))
        result = asyncio.run(forge._github_get(client, "https://api.github.com/repos/o/r"))
        assert result is None

    def test_params_forwarded(self, forge):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(200, []))
        asyncio.run(
            forge._github_get(
                client, "https://api.github.com/repos/o/r/issues", params={"state": "open"}
            )
        )
        _, kwargs = client.get.call_args
        assert kwargs["params"] == {"state": "open"}

    def test_network_exception_is_a_gap_not_a_negative(self, forge):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.ConnectError("boom"))
        result = asyncio.run(forge._github_get(client, "https://api.github.com/repos/o/r"))
        assert result is COLLECTION_GAP
