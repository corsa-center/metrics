"""Unit tests for RetryingTransport and GitHubCollectorBase's GitHub helpers.

RetryingTransport is the single, shared fix for the incident where a GitHub
secondary rate limit during a large concurrent collection run was silently
treated as "no data" instead of retried -- the root cause of stars/forks/
CHAOSS/governance metrics reading as zero or "not found" for the majority
of tracked packages. _check_file_exists and _github_get used to each retry
on their own; now that's the transport's job, so these tests split
accordingly: the transport owns the retry-policy tests, the two helpers only
need to prove they interpret a single response correctly.
"""

import asyncio
import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock

from collectors.ecosystem.base import (
    COLLECTION_GAP,
    GitHubCollectorBase,
    RepoTree,
    RetryingTransport,
    _repo_info_cache,
)


@pytest.fixture
def collector():
    return GitHubCollectorBase()


@pytest.fixture(autouse=True)
def _clear_shared_cache():
    """The repo-info dedup cache is module-level by design (see base.py) so
    it coalesces requests across different collector instances -- which
    means it persists across tests too unless cleared."""
    _repo_info_cache.clear()
    yield
    _repo_info_cache.clear()


def _resp(status_code, json_body=None, headers=None, text=""):
    r = MagicMock(spec=httpx.Response)
    r.status_code = status_code
    r.json.return_value = json_body
    # httpx.Headers is case-insensitive on lookup/`in`; a plain dict isn't,
    # which is what real GitHub responses (and RetryingTransport) rely on.
    r.headers = httpx.Headers(headers or {})
    r.text = text
    r.aread = AsyncMock()
    return r


def _request():
    return httpx.Request("GET", "https://api.github.com/repos/o/r")


class TestRepoInfoDeduping:
    """Second permanent fix, alongside the retry-volume tightening: at
    least 5 collectors independently re-fetch the same package's bare
    GET /repos/{owner}/{repo} within one run. Coalescing those into a
    single real request is free volume reduction with no staleness risk
    -- the whole point is these all want the exact same answer at
    essentially the exact same moment.
    """

    def test_concurrent_requests_for_same_url_share_one_real_fetch(self):
        wrapped = AsyncMock()
        wrapped.handle_async_request = AsyncMock(return_value=_resp(200, {"stars": 5}))
        transport = RetryingTransport(wrapped)

        async def run():
            return await asyncio.gather(
                *[transport.handle_async_request(_request()) for _ in range(5)]
            )

        responses = asyncio.run(run())
        assert all(r.status_code == 200 for r in responses)
        assert wrapped.handle_async_request.call_count == 1

    def test_sequential_requests_for_same_url_also_reuse_the_cached_result(self):
        wrapped = AsyncMock()
        wrapped.handle_async_request = AsyncMock(return_value=_resp(200, {"stars": 5}))
        transport = RetryingTransport(wrapped)

        asyncio.run(transport.handle_async_request(_request()))
        asyncio.run(transport.handle_async_request(_request()))
        assert wrapped.handle_async_request.call_count == 1

    def test_different_urls_are_not_conflated(self):
        wrapped = AsyncMock()
        wrapped.handle_async_request = AsyncMock(return_value=_resp(200, {}))
        transport = RetryingTransport(wrapped)

        asyncio.run(transport.handle_async_request(httpx.Request("GET", "https://api.github.com/repos/a/b")))
        asyncio.run(transport.handle_async_request(httpx.Request("GET", "https://api.github.com/repos/c/d")))
        assert wrapped.handle_async_request.call_count == 2

    def test_non_repo_info_endpoints_are_not_deduped(self):
        # Issues/PRs/releases listings legitimately vary by query params and
        # aren't confirmed-safe to coalesce -- only the bare repo-info shape is.
        wrapped = AsyncMock()
        wrapped.handle_async_request = AsyncMock(return_value=_resp(200, []))
        transport = RetryingTransport(wrapped)

        req = httpx.Request("GET", "https://api.github.com/repos/o/r/issues")
        asyncio.run(transport.handle_async_request(req))
        asyncio.run(transport.handle_async_request(req))
        assert wrapped.handle_async_request.call_count == 2

    def test_a_failed_fetch_is_cached_too_not_repeatedly_retried_by_every_caller(self):
        # If the one real request does exhaust its retries and fail, every
        # collector wanting this package's repo info should see that same
        # failure once, not each independently burn their own retry budget.
        wrapped = AsyncMock()
        wrapped.handle_async_request = AsyncMock(
            return_value=_resp(403, headers={"Retry-After": "0"})
        )
        transport = RetryingTransport(wrapped)

        async def run():
            return await asyncio.gather(
                *[transport.handle_async_request(_request()) for _ in range(3)]
            )

        responses = asyncio.run(run())
        assert all(r.status_code == 403 for r in responses)
        # _RETRY_ATTEMPTS=2 for the one real fetch, not 2 x 3 callers.
        assert wrapped.handle_async_request.call_count == 2


class TestRetryingTransport:
    def test_success_passes_through(self):
        wrapped = AsyncMock()
        wrapped.handle_async_request = AsyncMock(return_value=_resp(200, {"ok": True}))
        transport = RetryingTransport(wrapped)
        response = asyncio.run(transport.handle_async_request(_request()))
        assert response.status_code == 200
        assert wrapped.handle_async_request.call_count == 1

    def test_404_is_not_retried(self):
        wrapped = AsyncMock()
        wrapped.handle_async_request = AsyncMock(return_value=_resp(404))
        transport = RetryingTransport(wrapped)
        response = asyncio.run(transport.handle_async_request(_request()))
        assert response.status_code == 404
        assert wrapped.handle_async_request.call_count == 1

    def test_secondary_rate_limit_with_retry_after_header_is_retried(self):
        wrapped = AsyncMock()
        wrapped.handle_async_request = AsyncMock(
            side_effect=[_resp(403, headers={"Retry-After": "0"}), _resp(200, {"ok": True})]
        )
        transport = RetryingTransport(wrapped)
        response = asyncio.run(transport.handle_async_request(_request()))
        assert response.status_code == 200
        assert wrapped.handle_async_request.call_count == 2

    def test_403_without_retry_after_header_is_not_retried(self):
        # An earlier version also retried on "secondary rate limit"/"abuse"
        # wording in the body. That fired often enough that retrying every
        # hit (up to 3x, across ~20 collectors x 70+ packages) pushed total
        # request volume past GitHub's 5,000/hour quota and caused a worse
        # outcome -- near-total data loss -- than the original bug. Requiring
        # Retry-After specifically is a stricter, cheaper signal on purpose.
        wrapped = AsyncMock()
        wrapped.handle_async_request = AsyncMock(
            return_value=_resp(403, text="You have exceeded a secondary rate limit")
        )
        transport = RetryingTransport(wrapped)
        response = asyncio.run(transport.handle_async_request(_request()))
        assert response.status_code == 403
        assert wrapped.handle_async_request.call_count == 1

    def test_plain_403_is_not_retried(self):
        # A genuine permission error -- no Retry-After, no rate-limit wording
        # -- shouldn't burn retries waiting on a throttle that isn't real.
        wrapped = AsyncMock()
        wrapped.handle_async_request = AsyncMock(return_value=_resp(403, text="Forbidden"))
        transport = RetryingTransport(wrapped)
        response = asyncio.run(transport.handle_async_request(_request()))
        assert response.status_code == 403
        assert wrapped.handle_async_request.call_count == 1

    def test_429_is_retried_without_needing_a_message(self):
        wrapped = AsyncMock()
        wrapped.handle_async_request = AsyncMock(
            side_effect=[_resp(429), _resp(200, {"ok": True})]
        )
        transport = RetryingTransport(wrapped)
        response = asyncio.run(transport.handle_async_request(_request()))
        assert response.status_code == 200

    def test_5xx_is_retried(self):
        wrapped = AsyncMock()
        wrapped.handle_async_request = AsyncMock(
            side_effect=[_resp(503), _resp(200, {"ok": True})]
        )
        transport = RetryingTransport(wrapped)
        response = asyncio.run(transport.handle_async_request(_request()))
        assert response.status_code == 200

    def test_retries_are_bounded_then_returns_last_response(self):
        wrapped = AsyncMock()
        wrapped.handle_async_request = AsyncMock(
            return_value=_resp(403, headers={"Retry-After": "0"})
        )
        transport = RetryingTransport(wrapped)
        response = asyncio.run(transport.handle_async_request(_request()))
        assert response.status_code == 403
        assert wrapped.handle_async_request.call_count == 2


class TestCheckFileExists:
    """With retries owned by the transport, this only needs to interpret one response."""

    def _client(self, status_code, json_body=None):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(status_code, json_body))
        return client

    def test_file_found(self, collector):
        client = self._client(200, {"html_url": "https://github.com/o/r/blob/main/x"})
        result = asyncio.run(collector._check_file_exists(client, "o", "r", "x"))
        assert result == "https://github.com/o/r/blob/main/x"

    def test_directory_found(self, collector):
        client = self._client(200, [{"name": "a"}, {"name": "b"}])
        result = asyncio.run(collector._check_file_exists(client, "o", "r", "dir"))
        assert result == "https://github.com/o/r/tree/HEAD/dir"

    def test_not_found(self, collector):
        client = self._client(404)
        result = asyncio.run(collector._check_file_exists(client, "o", "r", "x"))
        assert result is None

    def test_final_failure_after_transport_retries_is_a_gap_not_a_negative(self, collector):
        # By the time this code sees the response, the transport has already
        # retried and given up. A 403 here is unknown, not "confirmed
        # absent" -- must not collapse into the same None a real 404 returns.
        client = self._client(403)
        result = asyncio.run(collector._check_file_exists(client, "o", "r", "x"))
        assert result is COLLECTION_GAP
        assert not result  # still falsy, so `if not result:` callers are unaffected

    def test_network_exception_is_a_gap_not_a_negative(self, collector):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.ConnectError("boom"))
        result = asyncio.run(collector._check_file_exists(client, "o", "r", "x"))
        assert result is COLLECTION_GAP


class TestGithubGet:
    def test_success_returns_json(self, collector):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(200, {"stargazers_count": 42}))
        result = asyncio.run(collector._github_get(client, "https://api.github.com/repos/o/r"))
        assert result == {"stargazers_count": 42}

    def test_404_returns_none(self, collector):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(404))
        result = asyncio.run(collector._github_get(client, "https://api.github.com/repos/o/r"))
        assert result is None

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

    def test_network_exception_is_a_gap_not_a_negative(self, collector):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.ConnectError("boom"))
        result = asyncio.run(collector._github_get(client, "https://api.github.com/repos/o/r"))
        assert result is COLLECTION_GAP


class TestRepoTree:
    def _tree(self, paths, truncated=False):
        return RepoTree("o", "r", paths, truncated)

    def _fetch(self, tree_response):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(200, tree_response))
        return asyncio.run(RepoTree.fetch(client, {}, "o", "r"))

    # -- match(): case-insensitive candidate-list lookup, replacing the old
    # case-sensitive _check_file_exists loop.

    def test_match_is_case_insensitive(self):
        # AMReX-Codes/amrex ships "GOVERNANCE.rst" with different casing than
        # a candidate spelled "governance.rst".
        tree = self._tree(["GOVERNANCE.rst", "README.md"])
        assert tree.match(["governance.rst", "GOVERNANCE.md"]) == "GOVERNANCE.rst"

    def test_match_returns_real_casing_not_the_candidate(self):
        tree = self._tree(["GNUmakefile.in"])
        assert tree.match(["Makefile", "GNUmakefile.in"]) == "GNUmakefile.in"

    def test_match_respects_candidate_order(self):
        tree = self._tree(["GOVERNANCE.md", "docs/governance.md"])
        assert tree.match(["GOVERNANCE.md", "docs/governance.md"]) == "GOVERNANCE.md"

    def test_match_none_when_nothing_present(self):
        tree = self._tree(["README.md"])
        assert tree.match(["GOVERNANCE.md", "docs/GOVERNANCE.md"]) is None

    def test_match_url_renders_a_browsable_link(self):
        tree = self._tree(["Docs/GettingStarted.rst"])
        assert tree.match_url(["docs/GettingStarted.rst"]) == (
            "https://github.com/o/r/blob/HEAD/Docs/GettingStarted.rst"
        )

    def test_match_url_none_when_unmatched(self):
        tree = self._tree(["README.md"])
        assert tree.match_url(["GOVERNANCE.md"]) is None

    def test_match_finds_a_vendored_directory(self):
        # "test/googletest" is a vendored subdirectory, not a file -- the
        # Contents API handled both the same way, so match() has to too.
        tree = self._tree(["test/googletest/googletest/include/gtest/gtest.h"])
        assert tree.match(["test/googletest"]) == "test/googletest"

    def test_match_url_renders_a_tree_link_for_a_directory_hit(self):
        tree = self._tree([".github/ISSUE_TEMPLATE/bug_report.md"])
        assert tree.match_url([".github/ISSUE_TEMPLATE"]) == (
            "https://github.com/o/r/tree/HEAD/.github/ISSUE_TEMPLATE"
        )

    def test_match_prefers_a_file_over_a_directory_of_the_same_name(self):
        # Degenerate case (a repo can't actually have both), but match()
        # should still resolve deterministically rather than pick either.
        tree = self._tree(["docs", "docs/x/y.md"])
        assert tree.match(["docs"]) == "docs"

    # -- has_dir(): case-insensitive directory presence.

    def test_has_dir_case_insensitive(self):
        # superlu ships DOC/, not doc/ or docs/ -- the candidate name "doc"
        # still has to find it.
        tree = self._tree(["DOC/html/index.html", "DOC/CMakeLists.txt"])
        assert tree.has_dir("doc") is True

    def test_has_dir_does_not_match_a_different_name(self):
        tree = self._tree(["DOC/html/index.html"])
        assert tree.has_dir("docs") is False

    def test_has_dir_false_when_absent(self):
        tree = self._tree(["README.md", "src/main.c"])
        assert tree.has_dir("docs") is False

    def test_has_dir_does_not_match_a_file_with_the_same_stem(self):
        # "doc" as a filename (no trailing slash) must not count as a "doc/"
        # directory.
        tree = self._tree(["doc"])
        assert tree.has_dir("doc") is False

    # -- find(): regex search over the whole tree, for "this concept, any
    # spelling" checks a literal list can't express.

    def test_find_matches_anywhere_in_the_tree(self):
        tree = self._tree([
            "Docs/sphinx_documentation/source/GettingStarted.rst",
            "src/main.c",
        ])
        hits = tree.find(r"getting[-_]?started")
        assert hits == ["Docs/sphinx_documentation/source/GettingStarted.rst"]

    def test_find_is_case_insensitive_by_default(self):
        tree = self._tree(["docs/GOVERNANCE.RST"])
        assert tree.find(r"governance\.rst") == ["docs/GOVERNANCE.RST"]

    def test_find_url_first_hit(self):
        tree = self._tree(["a/quickstart.md", "b/quickstart.md"])
        assert tree.find_url(r"quickstart") == "https://github.com/o/r/blob/HEAD/a/quickstart.md"

    def test_find_url_none_when_no_hits(self):
        tree = self._tree(["README.md"])
        assert tree.find_url(r"quickstart") is None

    # -- fetch(): the tree fetch itself.

    def test_fetch_extracts_blob_paths_only(self):
        tree = self._fetch({
            "tree": [
                {"path": "src", "type": "tree"},
                {"path": "src/main.c", "type": "blob"},
                {"path": "README.md", "type": "blob"},
            ],
            "truncated": False,
        })
        assert sorted(tree.paths) == ["README.md", "src/main.c"]
        assert tree.truncated is False

    def test_fetch_carries_truncated_flag(self):
        # llvm/llvm-project is large enough to hit this in practice.
        tree = self._fetch({"tree": [{"path": "a", "type": "blob"}], "truncated": True})
        assert tree.truncated is True

    def test_fetch_non_200_is_a_gap(self):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(404))
        result = asyncio.run(RepoTree.fetch(client, {}, "o", "r"))
        assert result is COLLECTION_GAP

    def test_fetch_network_exception_is_a_gap(self):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.ConnectError("boom"))
        result = asyncio.run(RepoTree.fetch(client, {}, "o", "r"))
        assert result is COLLECTION_GAP
