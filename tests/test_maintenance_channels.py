"""Unit tests for the 4.2.3 additions to ActiveMaintenanceCollector."""

import pytest

from collectors.ecosystem.active_maintenance import ActiveMaintenanceCollector


@pytest.fixture
def collector():
    return ActiveMaintenanceCollector()


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
    def _run(self, collector, status, items):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch
        resp = MagicMock(status_code=status)
        resp.json.return_value = {"items": items}
        client = MagicMock()
        client.get = AsyncMock(return_value=resp)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        with patch("collectors.ecosystem.active_maintenance.httpx.AsyncClient", return_value=client):
            return asyncio.run(collector._count_community_issues("o", "r")), client

    def test_counts_only_outside_authors(self, collector):
        items = [{"author_association": a} for a in ("NONE", "CONTRIBUTOR", "MEMBER", "OWNER", "FIRST_TIMER")]
        count, client = self._run(collector, 200, items)
        assert count == 3
        assert "is%3Aissue" in client.get.call_args.args[0]

    def test_failure_is_none_not_zero(self, collector):
        assert self._run(collector, 403, [])[0] is None
