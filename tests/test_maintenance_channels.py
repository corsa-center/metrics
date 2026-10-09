"""Unit tests for the 4.2.3 additions to ActiveMaintenanceCollector."""

import pytest

from collectors.ecosystem.active_maintenance import ActiveMaintenanceCollector
from tests.fakes import FakeForge


@pytest.fixture
def collector():
    return ActiveMaintenanceCollector(FakeForge())


def _weeks(prior, recent):
    """104 weeks: 52 of `prior` commits each, then 52 of `recent`."""
    return [{"c": prior}] * 52 + [{"c": recent}] * 52


class TestAbandonment:
    def test_departed_contributor_counted(self, collector):
        stats = [{"weeks": _weeks(3, 0)}, {"weeks": _weeks(3, 3)}]
        out = collector._analyze_abandonment(stats)
        assert out["previously_active"] == 2
        assert out["departed"] == 1
        assert out["departure_rate"] == 0.5

    def test_newcomers_do_not_count_as_previously_active(self, collector):
        # No commits in the earlier window: they cannot have departed.
        assert collector._analyze_abandonment([{"weeks": _weeks(0, 5)}])["measurable"] is False

    def test_short_history_is_skipped(self, collector):
        assert collector._analyze_abandonment(
            [{"weeks": [{"c": 1}] * 30}])["measurable"] is False

    def test_no_stats(self, collector):
        out = collector._analyze_abandonment([])
        assert out["measurable"] is False
        assert out["departure_rate"] is None

    def test_full_departure(self, collector):
        assert collector._analyze_abandonment([{"weeks": _weeks(2, 0)}])["departure_rate"] == 1.0


class TestChannels:
    def test_repo_flags(self, collector):
        out = collector._analyze_channels(
            {"has_discussions": True, "has_wiki": True}, "", wiki_has_content=True)
        assert out["found"] == ["GitHub Discussions", "Wiki"]

    def test_empty_wiki_is_not_a_channel(self, collector):
        # has_wiki is on by default for every repository, pages or not.
        out = collector._analyze_channels({"has_wiki": True}, "", wiki_has_content=False)
        assert "Wiki" not in out["found"]

    def test_issue_tracker_used_by_community_counts(self, collector):
        out = collector._analyze_channels({"has_issues": True}, "", community_issues=12)
        assert "GitHub Issues" in out["found"]
        assert out["community_issues_last_year"] == 12

    @pytest.mark.parametrize("repo_info,count", [
        ({"has_issues": True}, 2),      # too little outside traffic
        ({"has_issues": True}, None),   # couldn't be measured
        ({"has_issues": False}, 50),    # tracker disabled
    ])
    def test_issue_tracker_not_counted(self, collector, repo_info, count):
        out = collector._analyze_channels(repo_info, "", community_issues=count)
        assert "GitHub Issues" not in out["found"]

    @pytest.mark.parametrize("text,label", [
        ("Join our mailing list at groups.google.com/g/x", "Mailing list"),
        ("Chat with us on https://slack.com/x", "Chat (Slack/Discord/Matrix)"),
        ("Ask on our forum", "Forum"),
        ("File a ticket in Jira", "Help desk"),
    ])
    def test_readme_channels(self, collector, text, label):
        assert label in collector._analyze_channels({}, text)["found"]

    def test_plain_readme_finds_nothing(self, collector):
        assert collector._analyze_channels({}, "A fast I/O library.")["count"] == 0

    def test_count_matches_found(self, collector):
        out = collector._analyze_channels({"has_discussions": True},
                                          "our mailing list and forum")
        assert out["count"] == len(out["found"]) == 3


class TestCountCommunityIssues:
    def _run(self, pages):
        import asyncio
        from tests.fakes import FakeForge
        forge = FakeForge(recent_issue_pages=pages)
        return asyncio.run(ActiveMaintenanceCollector(forge)._count_community_issues(None, "o/r")), forge

    @staticmethod
    def _page(outside, inside):
        return [{"is_outsider": True}] * outside + [{"is_outsider": False}] * inside

    def test_counts_only_outside_authors(self):
        count, _ = self._run([self._page(3, 2)])
        assert count == 3

    def test_failure_is_none_not_zero(self):
        assert self._run([None])[0] is None


class TestCountCommunityIssuesPaging:
    """Maintainer-filed tickets can fill the newest 100 issues (AMReX: 98 of
    100) while the year holds plenty from outside -- keep paging."""

    _page = staticmethod(TestCountCommunityIssues._page)

    def _run(self, pages):
        return TestCountCommunityIssues._run(self, pages)

    def test_pages_past_a_maintainer_filled_first_page(self):
        count, _ = self._run([self._page(2, 98), self._page(10, 90)])
        assert count == 12

    def test_stops_once_the_threshold_is_met(self):
        count, _ = self._run([self._page(6, 94), self._page(50, 50)])
        assert count == 6

    def test_stops_on_a_short_page(self):
        count, _ = self._run([self._page(1, 30), self._page(50, 50)])
        assert count == 1

    def test_later_page_failure_keeps_the_lower_bound(self):
        count, _ = self._run([self._page(2, 98), None])
        assert count == 2


class TestRelatedRepositories:
    def test_contributor_who_moved_repos_is_not_departed(self, collector):
        weeks_old = [{"w": i, "c": 1 if i < 52 else 0} for i in range(104)]
        weeks_new = [{"w": i, "c": 1 if i >= 52 else 0} for i in range(104)]
        main = [{"author": {"login": "Alice"}, "weeks": weeks_old}]
        companion = [{"author": {"login": "alice"}, "weeks": weeks_new}]
        alone = collector._analyze_abandonment(main)
        merged = collector._analyze_abandonment(collector._merge_contributor_stats([main, companion]))
        assert alone["departed"] == 1
        assert merged["departed"] == 0 and merged["previously_active"] == 1

    def test_related_repositories_are_read_from_catalog_entry(self):
        import asyncio
        from tests.fakes import FakeForge
        calls = []
        forge = FakeForge()

        async def stats(client, ref):
            calls.append(ref)
            return []
        forge.contributor_weekly_stats = stats
        asyncio.run(ActiveMaintenanceCollector(forge).collect({
            "name": "spack", "repo_url": "https://github.com/spack/spack",
            "related_repositories": ["spack/spack-packages"]}))
        assert calls == ["o/r", "spack/spack-packages"]


class TestVersionTagsAsReleases:
    def _run(self, releases, graphql):
        import asyncio
        from unittest.mock import MagicMock, patch

        class Resp:
            def __init__(self, status, data):
                self.status_code, self._data = status, data

            def json(self):
                return self._data

        class Client:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, headers=None, params=None):
                return Resp(200, releases)

            async def post(self, url, headers=None, json=None):
                return Resp(200, graphql)

        from forge.github import GitHubForge
        c = ActiveMaintenanceCollector(GitHubForge("token"))
        return asyncio.run(c._get_releases(Client(), "o/r"))

    @staticmethod
    def _tags(*items):
        return {"data": {"repository": {"refs": {"nodes": [
            {"name": n, "target": {"__typename": "Commit", "committedDate": d}} for n, d in items]}}}}

    def test_tag_only_versions_count(self):
        rel = self._run([], self._tags(("v5.0.11", "2026-08-26T00:00:00Z"),
                                       ("v5.0.11rc1", "2026-08-01T00:00:00Z"),
                                       ("v5.0.10", "2026-02-01T00:00:00Z")))
        assert [r["tag_name"] for r in rel] == ["v5.0.11", "v5.0.10"]

    def test_tags_already_released_are_not_doubled(self):
        rel = self._run([{"tag_name": "v2.0", "published_at": "2026-05-01T00:00:00Z"}],
                        self._tags(("v2.0", "2026-04-30T00:00:00Z"), ("v1.9", "2025-12-01T00:00:00Z")))
        assert [r["tag_name"] for r in rel] == ["v2.0", "v1.9"]

    def test_annotated_tag_uses_its_own_date(self):
        g = {"data": {"repository": {"refs": {"nodes": [{"name": "v1.0", "target": {
            "__typename": "Tag", "tagger": {"date": "2026-01-02T00:00:00Z"},
            "target": {"committedDate": "2025-06-01T00:00:00Z"}}}]}}}}
        assert self._run([], g)[0]["published_at"] == "2026-01-02T00:00:00Z"

    def test_compatibility_snapshots_are_not_releases(self):
        rel = self._run([], self._tags(("compass-2026-03-21", "2026-03-21T00:00:00Z"),
                                       ("release-2022.05.15", "2022-05-15T00:00:00Z")))
        assert [r["tag_name"] for r in rel] == ["release-2022.05.15"]


class TestPlatformLabels:
    def test_gitlab_tracker_is_labelled_gitlab(self):
        from tests.fakes import gitlab_fake
        out = ActiveMaintenanceCollector(gitlab_fake())._analyze_channels(
            {"has_issues": True}, "", community_issues=100)
        assert "GitLab Issues" in out["found"]
        assert not any("GitHub" in c for c in out["found"])
