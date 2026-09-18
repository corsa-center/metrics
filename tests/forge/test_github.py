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
from forge.github import GitHubForge, _is_bot_login


@pytest.fixture
def forge():
    return GitHubForge()


class TestIsBotLogin:
    def test_github_actions(self):
        assert _is_bot_login("github-actions[bot]") is True

    def test_renovate(self):
        assert _is_bot_login("renovate-bot") is True

    def test_human(self):
        assert _is_bot_login("octocat") is False


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


class TestRepoInfo:
    def _client(self, json_body):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(200, json_body))
        return client

    def test_normalizes_field_names(self, forge):
        client = self._client({
            "stargazers_count": 42, "forks_count": 7, "subscribers_count": 3,
            "open_issues_count": 5, "description": "desc", "homepage": " https://x ",
            "has_wiki": True, "has_pages": False, "has_discussions": True,
            "archived": False, "disabled": False, "language": "Python",
            "size": 1024, "created_at": "2020-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z", "pushed_at": "2026-01-01T00:00:00Z",
            "default_branch": "main",
            "license": {"spdx_id": "MIT", "name": "MIT License", "key": "mit"},
        })
        result = asyncio.run(forge.repo_info(client, "o/r"))
        assert result["stars"] == 42
        assert result["forks"] == 7
        assert result["watchers"] == 3
        assert result["open_issues"] == 5
        assert result["homepage"] == "https://x"
        assert result["has_discussions"] is True
        assert result["license"] == {"spdx_id": "MIT", "name": "MIT License", "key": "mit"}

    def test_missing_license_is_none(self, forge):
        client = self._client({"stargazers_count": 0})
        result = asyncio.run(forge.repo_info(client, "o/r"))
        assert result["license"] is None

    def test_blank_homepage_is_none(self, forge):
        client = self._client({"homepage": "   "})
        result = asyncio.run(forge.repo_info(client, "o/r"))
        assert result["homepage"] is None

    def test_404_returns_none(self, forge):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(404))
        result = asyncio.run(forge.repo_info(client, "o/r"))
        assert result is None

    def test_gap_passes_through(self, forge):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(403))
        result = asyncio.run(forge.repo_info(client, "o/r"))
        assert result is COLLECTION_GAP


class TestCommits:
    def _client(self, json_body):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(200, json_body))
        return client

    def test_unwraps_nested_commit_shape(self, forge):
        client = self._client([{
            "sha": "abc123",
            "commit": {
                "message": "Fix the thing\n\nLonger body here.",
                "author": {"name": "Jane Doe", "date": "2026-01-01T00:00:00Z"},
            },
            "author": {"login": "janedoe"},
        }])
        result = asyncio.run(forge.commits(client, "o/r"))
        assert result == [{
            "sha": "abc123",
            "message": "Fix the thing\n\nLonger body here.",
            "subject": "Fix the thing",
            "author_identity": "janedoe",
            "date": "2026-01-01T00:00:00Z",
        }]

    def test_falls_back_to_commit_author_name_without_github_account(self, forge):
        client = self._client([{
            "sha": "abc123",
            "commit": {"message": "x", "author": {"name": "Jane Doe", "date": "d"}},
            "author": None,
        }])
        result = asyncio.run(forge.commits(client, "o/r"))
        assert result[0]["author_identity"] == "Jane Doe"

    def test_gap_passes_through(self, forge):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(403))
        result = asyncio.run(forge.commits(client, "o/r"))
        assert result is COLLECTION_GAP


class TestRepoTree:
    def test_filters_to_blobs_and_carries_truncated_flag(self, forge):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(200, {
            "tree": [
                {"path": "src/a.c", "type": "blob", "size": 100},
                {"path": "src", "type": "tree"},
            ],
            "truncated": True,
        }))
        result = asyncio.run(forge.repo_tree(client, "o/r"))
        assert result == {"files": [{"path": "src/a.c", "size": 100}], "truncated": True}

    def test_gap_passes_through(self, forge):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(403))
        result = asyncio.run(forge.repo_tree(client, "o/r"))
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
