"""Unit tests for the 4.2.3 additions to ActiveMaintenanceCollector."""

import pytest

from collectors.sustainability.active_maintenance import ActiveMaintenanceCollector


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
        out = collector._analyze_channels({"has_discussions": True, "has_wiki": True}, "")
        assert out["found"] == ["GitHub Discussions", "Wiki"]

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
