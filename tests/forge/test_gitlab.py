"""Unit tests for GitLabForge -- see tests/forge/test_github.py for the
matching GitHub-side coverage of the same semantic interface.
"""

import asyncio
import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock

from forge.base import COLLECTION_GAP
from forge.gitlab import GitLabForge


@pytest.fixture
def forge():
    return GitLabForge(api_base="https://gitlab.example.com/api/v4")


def _resp(status_code, json_body=None, headers=None):
    r = MagicMock(spec=httpx.Response)
    r.status_code = status_code
    r.json.return_value = json_body
    r.headers = httpx.Headers(headers or {})
    return r


def _client(json_body, status_code=200):
    client = AsyncMock()
    client.get = AsyncMock(return_value=_resp(status_code, json_body))
    return client


class TestConstruction:
    def test_host_derived_from_api_base(self):
        forge = GitLabForge(api_base="https://gitlab.kitware.com/api/v4")
        assert forge.host == "gitlab.kitware.com"

    def test_default_api_base_is_gitlab_com(self):
        forge = GitLabForge()
        assert forge.host == "gitlab.com"

    def test_token_sets_private_token_header(self):
        forge = GitLabForge(token="secret")
        assert forge.headers["PRIVATE-TOKEN"] == "secret"

    def test_no_token_omits_private_token_header(self):
        forge = GitLabForge()
        assert "PRIVATE-TOKEN" not in forge.headers


class TestExtractRef:
    def test_matches_own_host(self, forge):
        assert forge.extract_ref("https://gitlab.example.com/group/project") == "group/project"

    def test_nested_subgroups(self, forge):
        assert forge.extract_ref(
            "https://gitlab.example.com/group/subgroup/project"
        ) == "group/subgroup/project"

    def test_git_suffix_stripped(self, forge):
        assert forge.extract_ref("https://gitlab.example.com/group/project.git") == "group/project"

    def test_trailing_slash_stripped(self, forge):
        assert forge.extract_ref("https://gitlab.example.com/group/project/") == "group/project"

    def test_different_host_is_none(self, forge):
        # A forge built for gitlab.example.com must not silently accept a
        # gitlab.com (or any other host's) URL.
        assert forge.extract_ref("https://gitlab.com/group/project") is None

    def test_github_url_is_none(self, forge):
        assert forge.extract_ref("https://github.com/owner/repo") is None

    def test_empty_is_none(self, forge):
        assert forge.extract_ref("") is None


class TestGitlabGet:
    def test_success_returns_json(self, forge):
        client = _client({"id": 1})
        result = asyncio.run(forge._gitlab_get(client, "/projects/x"))
        assert result == {"id": 1}

    def test_404_returns_none(self, forge):
        client = _client(None, status_code=404)
        result = asyncio.run(forge._gitlab_get(client, "/projects/x"))
        assert result is None

    def test_other_non_200_is_a_gap(self, forge):
        client = _client(None, status_code=403)
        result = asyncio.run(forge._gitlab_get(client, "/projects/x"))
        assert result is COLLECTION_GAP

    def test_network_exception_is_a_gap(self, forge):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.ConnectError("boom"))
        result = asyncio.run(forge._gitlab_get(client, "/projects/x"))
        assert result is COLLECTION_GAP

    def test_url_is_relative_to_api_base(self, forge):
        client = _client({})
        asyncio.run(forge._gitlab_get(client, "/projects/x"))
        args, _ = client.get.call_args
        assert args[0] == "https://gitlab.example.com/api/v4/projects/x"


class TestRepoInfo:
    def test_normalizes_field_names(self, forge):
        client = _client({
            "star_count": 10, "forks_count": 3, "open_issues_count": 5,
            "description": "desc", "wiki_enabled": True,
            "pages_access_level": "enabled", "archived": False,
            "created_at": "2020-01-01T00:00:00Z",
            "last_activity_at": "2026-01-01T00:00:00Z",
            "default_branch": "main",
            "statistics": {"repository_size": 2048},
            "license": {"key": "mit", "name": "MIT License"},
        })
        result = asyncio.run(forge.repo_info(client, "g/p"))
        assert result["stars"] == 10
        assert result["forks"] == 3
        assert result["watchers"] == 0
        assert result["open_issues"] == 5
        assert result["has_wiki"] is True
        assert result["has_pages"] is True
        assert result["has_discussions"] is False
        assert result["homepage"] is None
        assert result["language"] is None
        assert result["updated_at"] == "2026-01-01T00:00:00Z"
        assert result["pushed_at"] == "2026-01-01T00:00:00Z"
        assert result["size_kb"] == 2
        assert result["license"] == {"spdx_id": None, "name": "MIT License", "key": "mit"}

    def test_pages_disabled_is_false(self, forge):
        client = _client({"pages_access_level": "disabled"})
        result = asyncio.run(forge.repo_info(client, "g/p"))
        assert result["has_pages"] is False

    def test_no_license_is_none(self, forge):
        client = _client({"license": None})
        result = asyncio.run(forge.repo_info(client, "g/p"))
        assert result["license"] is None

    def test_gap_passes_through(self, forge):
        client = _client(None, status_code=403)
        result = asyncio.run(forge.repo_info(client, "g/p"))
        assert result is COLLECTION_GAP

    def test_404_is_none(self, forge):
        client = _client(None, status_code=404)
        result = asyncio.run(forge.repo_info(client, "g/p"))
        assert result is None


class TestFileContent:
    def test_decodes_base64(self, forge):
        import base64
        client = _client({"content": base64.b64encode(b"hello").decode(), "encoding": "base64"})
        result = asyncio.run(forge.file_content(client, "g/p", "README.md"))
        assert result == "hello"

    def test_404_is_none(self, forge):
        client = _client(None, status_code=404)
        result = asyncio.run(forge.file_content(client, "g/p", "MISSING.md"))
        assert result is None

    def test_gap_passes_through(self, forge):
        client = _client(None, status_code=403)
        result = asyncio.run(forge.file_content(client, "g/p", "README.md"))
        assert result is COLLECTION_GAP


class TestFileExists:
    def test_found_returns_url(self, forge):
        client = _client({"size": 10, "ref": "main"})
        result = asyncio.run(forge.file_exists(client, "g/p", "LICENSE"))
        assert result == "https://gitlab.example.com/g/p/-/blob/main/LICENSE"

    def test_not_found_is_none(self, forge):
        client = _client(None, status_code=404)
        result = asyncio.run(forge.file_exists(client, "g/p", "LICENSE"))
        assert result is None

    def test_gap_passes_through(self, forge):
        client = _client(None, status_code=403)
        result = asyncio.run(forge.file_exists(client, "g/p", "LICENSE"))
        assert result is COLLECTION_GAP


class TestCommits:
    def test_flattens_gitlab_shape(self, forge):
        client = _client([{
            "id": "abc123", "title": "Fix the thing", "message": "Fix the thing\n\nBody.",
            "author_name": "Jane Doe",
            "authored_date": "2026-01-01T00:00:00Z",
            "committed_date": "2026-01-02T00:00:00Z",
        }])
        result = asyncio.run(forge.commits(client, "g/p"))
        assert result == [{
            "sha": "abc123",
            "message": "Fix the thing\n\nBody.",
            "subject": "Fix the thing",
            "author_identity": "Jane Doe",
            "date": "2026-01-01T00:00:00Z",
            "committer_date": "2026-01-02T00:00:00Z",
        }]

    def test_gap_passes_through(self, forge):
        client = _client(None, status_code=403)
        result = asyncio.run(forge.commits(client, "g/p"))
        assert result is COLLECTION_GAP


class TestContributors:
    def test_normalizes_to_identity_and_commit_count(self, forge):
        client = _client([{"name": "Jane Doe", "commits": 42}])
        result = asyncio.run(forge.contributors(client, "g/p"))
        assert result == [{"identity": "Jane Doe", "commit_count": 42}]


class TestFirstCommitDate:
    def test_returns_committed_date_of_oldest(self, forge):
        client = _client([{"committed_date": "1997-07-30T21:17:56Z"}])
        result = asyncio.run(forge.first_commit_date(client, "g/p"))
        assert result == "1997-07-30T21:17:56Z"

    def test_empty_history_is_none(self, forge):
        client = _client([])
        result = asyncio.run(forge.first_commit_date(client, "g/p"))
        assert result is None

    def test_gap_passes_through(self, forge):
        client = _client(None, status_code=403)
        result = asyncio.run(forge.first_commit_date(client, "g/p"))
        assert result is COLLECTION_GAP


class TestDirListing:
    def test_maps_blob_and_tree_types(self, forge):
        client = _client([
            {"name": "a.md", "path": "docs/a.md", "type": "blob"},
            {"name": "sub", "path": "docs/sub", "type": "tree"},
        ])
        result = asyncio.run(forge.dir_listing(client, "g/p", "docs"))
        assert result[0]["type"] == "file"
        assert result[1]["type"] == "dir"

    def test_gap_passes_through(self, forge):
        client = _client(None, status_code=403)
        result = asyncio.run(forge.dir_listing(client, "g/p", "docs"))
        assert result is COLLECTION_GAP


class TestReleasesAndTags:
    def test_releases_aliases_published_at(self, forge):
        client = _client([{"tag_name": "v1.0", "released_at": "2026-01-01T00:00:00Z"}])
        result = asyncio.run(forge.releases(client, "g/p"))
        assert result[0]["published_at"] == "2026-01-01T00:00:00Z"

    def test_tags_pass_through(self, forge):
        client = _client([{"name": "v1.0"}])
        result = asyncio.run(forge.tags(client, "g/p"))
        assert result == [{"name": "v1.0"}]


class TestCiRuns:
    def test_status_filter_translated_to_gitlab_vocabulary(self, forge):
        client = _client([])
        asyncio.run(forge.ci_runs(client, "g/p", status="failure"))
        _, kwargs = client.get.call_args
        assert kwargs["params"]["status"] == "failed"

    def test_branch_maps_to_ref_param(self, forge):
        client = _client([])
        asyncio.run(forge.ci_runs(client, "g/p", branch="main"))
        _, kwargs = client.get.call_args
        assert kwargs["params"]["ref"] == "main"

    def test_normalizes_pipeline_status(self, forge):
        client = _client([{"status": "success", "created_at": "a", "updated_at": "b"}])
        result = asyncio.run(forge.ci_runs(client, "g/p"))
        assert result == [{"status": "success", "conclusion": "success",
                           "created_at": "a", "updated_at": "b"}]


class TestCiWorkflows:
    def test_always_empty(self, forge):
        assert asyncio.run(forge.ci_workflows(None, "g/p")) == []

    def test_workflow_runs_always_empty(self, forge):
        assert asyncio.run(forge.ci_workflow_runs(None, "g/p", 1)) == []


class TestDeployments:
    def test_status_smuggled_through_statuses_url_field(self, forge):
        client = _client([{"created_at": "a", "status": "success"}])
        result = asyncio.run(forge.deployments(client, "g/p"))
        assert result == [{"created_at": "a", "statuses_url": "success"}]

    def test_deployment_succeeded_reads_the_smuggled_status_no_http_call(self, forge):
        assert asyncio.run(forge.deployment_succeeded(None, "success")) is True
        assert asyncio.run(forge.deployment_succeeded(None, "failed")) is False


class TestPrReviews:
    def test_returns_approved_by_list(self, forge):
        client = _client({"approved_by": [{"user": {"username": "alice"}}]})
        result = asyncio.run(forge.pr_reviews(client, "g/p", 1))
        assert len(result) == 1

    def test_no_approvals_is_empty_list_not_none(self, forge):
        client = _client({"approved_by": []})
        result = asyncio.run(forge.pr_reviews(client, "g/p", 1))
        assert result == []
        assert bool(result) is False


class TestSearchIssues:
    def test_always_none(self, forge):
        result = asyncio.run(forge.search_issues(None, "anything"))
        assert result is None


class TestUser:
    def test_returns_first_match(self, forge):
        client = _client([{"username": "alice", "id": 1}])
        result = asyncio.run(forge.user(client, "alice"))
        assert result == {"username": "alice", "id": 1}

    def test_no_match_is_none(self, forge):
        client = _client([])
        result = asyncio.run(forge.user(client, "nobody"))
        assert result is None

    def test_gap_passes_through(self, forge):
        client = _client(None, status_code=403)
        result = asyncio.run(forge.user(client, "alice"))
        assert result is COLLECTION_GAP


class TestCommunityProfile:
    def test_always_empty_dict(self, forge):
        assert asyncio.run(forge.community_profile(None, "g/p")) == {}


class TestPagesUrl:
    def test_top_level_group(self, forge):
        assert forge.pages_url("group/project") == "https://group.gitlab.io/project/"

    def test_nested_subgroup(self, forge):
        assert forge.pages_url("group/subgroup/project") == "https://group.gitlab.io/subgroup/project/"


class TestIssuesAndPullRequests:
    def test_state_open_translated_to_opened(self, forge):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=[
            _resp(200, []),  # issues
            _resp(200, []),  # members (only fetched if issues non-empty -- see below)
        ])
        asyncio.run(forge.issues(client, "g/p", state="open"))
        args, kwargs = client.get.call_args_list[0]
        assert kwargs["params"]["state"] == "opened"

    def test_merged_and_locked_collapse_to_closed(self, forge):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=[
            _resp(200, [
                {"iid": 1, "state": "merged", "author": {"username": "alice"}},
                {"iid": 2, "state": "locked", "author": {"username": "bob"}},
                {"iid": 3, "state": "opened", "author": {"username": "carol"}},
            ]),
            _resp(200, [{"username": "alice"}, {"username": "bob"}, {"username": "carol"}]),
        ])
        result = asyncio.run(forge.pull_requests(client, "g/p"))
        assert [r["state"] for r in result] == ["closed", "closed", "open"]

    def test_is_outsider_computed_from_membership(self, forge):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=[
            _resp(200, [
                {"iid": 1, "state": "opened", "author": {"username": "member"}},
                {"iid": 2, "state": "opened", "author": {"username": "outsider"}},
            ]),
            _resp(200, [{"username": "member"}]),
        ])
        result = asyncio.run(forge.issues(client, "g/p"))
        assert result[0]["is_outsider"] is False
        assert result[1]["is_outsider"] is True

    def test_number_comes_from_iid_not_id(self, forge):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=[
            _resp(200, [{"id": 9999, "iid": 3, "state": "opened", "author": {"username": "a"}}]),
            _resp(200, []),
        ])
        result = asyncio.run(forge.issues(client, "g/p"))
        assert result[0]["number"] == 3

    def test_comments_mapped_from_user_notes_count(self, forge):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=[
            _resp(200, [{"iid": 1, "state": "opened", "author": {"username": "a"},
                        "user_notes_count": 7}]),
            _resp(200, []),
        ])
        result = asyncio.run(forge.issues(client, "g/p"))
        assert result[0]["comments"] == 7

    def test_members_cache_reused_across_calls(self, forge):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=[
            _resp(200, [{"iid": 1, "state": "opened", "author": {"username": "a"}}]),
            _resp(200, [{"username": "a"}]),
            _resp(200, [{"iid": 2, "state": "opened", "author": {"username": "a"}}]),
        ])
        asyncio.run(forge.issues(client, "g/p"))
        asyncio.run(forge.pull_requests(client, "g/p"))
        # Only 3 calls total: issues, members (once), pull_requests -- the
        # second members lookup is served from the per-instance cache.
        assert client.get.call_count == 3

    def test_gap_passes_through_without_fetching_members(self, forge):
        client = _client(None, status_code=403)
        result = asyncio.run(forge.issues(client, "g/p"))
        assert result is COLLECTION_GAP
        assert client.get.call_count == 1


class TestIssueComments:
    def test_excludes_system_notes(self, forge):
        client = _client([
            {"system": True, "author": {"username": "a"}, "created_at": "x"},
            {"system": False, "author": {"username": "b"}, "created_at": "y"},
        ])
        result = asyncio.run(forge.issue_comments(client, "g/p", 1))
        assert len(result) == 1
        assert result[0]["author"] == "b"

    def test_bot_detection(self, forge):
        client = _client([
            {"system": False, "author": {"username": "renovate-bot", "bot": True}, "created_at": "x"},
            {"system": False, "author": {"username": "human"}, "created_at": "y"},
        ])
        result = asyncio.run(forge.issue_comments(client, "g/p", 1))
        assert result[0]["is_bot"] is True
        assert result[1]["is_bot"] is False

    def test_gap_is_empty_list(self, forge):
        client = _client(None, status_code=403)
        result = asyncio.run(forge.issue_comments(client, "g/p", 1))
        assert result == []


class TestCommitParticipation:
    def test_buckets_commits_into_52_weeks(self, forge):
        # Real "now" (not a fixed date), so the commit always lands in the
        # most recent bucket regardless of when the test runs.
        import datetime as dt
        now = dt.datetime.now(dt.timezone.utc).isoformat()

        client = AsyncMock()
        # commits() paginates with since=~1yr ago; one page with the commit,
        # then empty pages until pagination stops.
        client.get = AsyncMock(side_effect=[_resp(200, [
            {"id": "a", "title": "x", "authored_date": now, "committed_date": now}
        ])] + [_resp(200, [])] * 20)
        result = asyncio.run(forge.commit_participation(client, "g/p"))
        assert sum(result["all"]) == 1
        assert result["all"][-1] == 1  # most recent week bucket

    def test_no_commits_is_empty_dict(self, forge):
        client = _client([])
        result = asyncio.run(forge.commit_participation(client, "g/p"))
        assert result == {}


class TestContributorWeeklyStats:
    def test_groups_by_author_into_weekly_buckets(self, forge):
        now = "2026-06-15T00:00:00+00:00"
        client = AsyncMock()
        client.get = AsyncMock(side_effect=[_resp(200, [
            {"id": "a", "title": "x", "author_name": "Jane",
             "authored_date": now, "committed_date": now},
        ])] + [_resp(200, [])] * 20)
        result = asyncio.run(forge.contributor_weekly_stats(client, "g/p"))
        assert len(result) == 1
        assert result[0]["author"]["login"] == "Jane"
        assert sum(w["c"] for w in result[0]["weeks"]) == 1

    def test_no_commits_is_empty_list(self, forge):
        client = _client([])
        result = asyncio.run(forge.contributor_weekly_stats(client, "g/p"))
        assert result == []
