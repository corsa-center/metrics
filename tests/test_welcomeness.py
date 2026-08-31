"""Unit tests for WelcomenessCollector (CASS Section 4.2.6)."""

import pytest

from collectors.sustainability.welcomeness import WelcomenessCollector


@pytest.fixture
def collector():
    return WelcomenessCollector()


class TestScoring:
    def _score(self, collector, channels=(), docs=()):
        return collector._calculate_score(list(channels), {"found": list(docs)})

    def test_six_submetrics_stay_uncollected(self, collector):
        sub = self._score(collector)["sub_scores"]
        assert sum(1 for v in sub.values() if v.get("not_collected")) == 6
        assert self._score(collector)["max_score"] == 7

    def test_one_signal_is_not_enough(self, collector):
        s = self._score(collector, channels=["Wiki"])
        assert not s["sub_scores"]["decision_making_visibility"]["passing"]

    def test_two_signals_pass(self, collector):
        s = self._score(collector, channels=["Wiki", "GitHub Discussions"])
        assert s["sub_scores"]["decision_making_visibility"]["passing"]
        assert s["score"] == 1

    def test_channels_and_documents_both_count(self, collector):
        s = self._score(collector, channels=["Wiki"], docs=["Roadmap"])
        info = s["sub_scores"]["decision_making_visibility"]
        assert info["passing"]
        assert info["detail"] == "Wiki, Roadmap"

    def test_no_signals(self, collector):
        info = self._score(collector)["sub_scores"]["decision_making_visibility"]
        assert not info["passing"]
        assert "No public decision-making channels" in info["value"]
        assert info["detail"] is None


class TestEmptyResult:
    def test_invalid_url(self, collector):
        import asyncio
        r = asyncio.run(collector.collect({"name": "x", "repo_url": "nope"}))
        assert r["public_channels"] == []
        assert r["overall_score"]["max_score"] == 7
