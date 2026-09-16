"""Unit tests for GitHubClient.get_repository's secondary-rate-limit retry.

Regression coverage for the incident where PyGithub's urllib3 Retry
deliberately excludes 403 (to avoid its own multi-thousand-second backoff on
primary quota exhaustion), which meant a GitHub *secondary* rate limit
during a large concurrent run permanently failed that call with no retry --
the root cause of stars/forks reading as zero for most tracked packages.
"""

import asyncio
import pytest
from unittest.mock import MagicMock

from github.GithubException import GithubException

from integrations.github_api import GitHubClient


@pytest.fixture
def client():
    c = GitHubClient({})
    c.client = MagicMock()
    return c


def _exc(status, message="", headers=None):
    return GithubException(status, {"message": message}, headers or {})


class TestGetRepositoryRetry:
    def test_secondary_rate_limit_with_retry_after_is_retried(self, client):
        repo = MagicMock()
        client.client.get_repo = MagicMock(
            side_effect=[_exc(403, headers={"retry-after": "0"}), repo]
        )
        result = asyncio.run(client.get_repository("https://github.com/kokkos/kokkos"))
        assert result is repo
        assert client.client.get_repo.call_count == 2

    def test_message_alone_without_retry_after_is_not_retried(self, client):
        # An earlier version also retried on "secondary rate limit"/"abuse"
        # wording alone. That fired often enough that retrying every hit
        # (up to 3x, across a full portfolio run) pushed total request
        # volume past GitHub's 5,000/hour quota -- a worse outcome than the
        # original bug. Requiring Retry-After specifically is stricter and
        # retries less often, on purpose.
        client.client.get_repo = MagicMock(
            side_effect=_exc(403, message="You have exceeded a secondary rate limit")
        )
        with pytest.raises(GithubException):
            asyncio.run(client.get_repository("https://github.com/kokkos/kokkos"))
        assert client.client.get_repo.call_count == 1

    def test_plain_403_is_not_retried(self, client):
        # A genuine permission error (private/inaccessible repo) shouldn't
        # burn retries -- no Retry-After, no rate-limit wording.
        client.client.get_repo = MagicMock(side_effect=_exc(403, message="Forbidden"))
        with pytest.raises(GithubException):
            asyncio.run(client.get_repository("https://github.com/o/private-repo"))
        assert client.client.get_repo.call_count == 1

    def test_retries_are_bounded(self, client):
        client.client.get_repo = MagicMock(
            side_effect=_exc(403, headers={"retry-after": "0"})
        )
        with pytest.raises(GithubException):
            asyncio.run(client.get_repository("https://github.com/o/r"))
        assert client.client.get_repo.call_count == 2

    def test_404_is_not_retried(self, client):
        client.client.get_repo = MagicMock(side_effect=_exc(404, message="Not Found"))
        with pytest.raises(GithubException):
            asyncio.run(client.get_repository("https://github.com/o/does-not-exist"))
        assert client.client.get_repo.call_count == 1

    def test_invalid_url_raises_before_any_retry(self, client):
        client.client.get_repo = MagicMock()
        with pytest.raises(ValueError):
            asyncio.run(client.get_repository("not-a-github-url"))
        client.client.get_repo.assert_not_called()
