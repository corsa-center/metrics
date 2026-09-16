"""Unit tests for WelcomenessCollector (CASS Section 4.2.6)."""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from collectors.ecosystem.base import COLLECTION_GAP
from collectors.ecosystem.welcomeness import WelcomenessCollector, _DECISION_PATHS


@pytest.fixture
def collector():
    return WelcomenessCollector()


class TestScoring:
    def _score(self, collector, channels=(), docs=()):
        return collector._calculate_score(list(channels), {"found": list(docs)})

    def test_six_submetrics_stay_uncollected(self, collector):
        sub = self._score(collector)["sub_scores"]
        assert sum(1 for v in sub.values() if v.get("not_collected")) == 6
        # The 6 permanently-uncollected submetrics must not inflate the
        # denominator -- only decision_making_visibility is ever scorable.
        assert self._score(collector)["max_score"] == 1

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
        assert r["overall_score"]["max_score"] == 1


class TestScoringGapHandling:
    def test_below_threshold_under_channels_gap_is_not_collected(self, collector):
        s = collector._calculate_score([], {"found": []}, channels_gap=True)
        entry = s["sub_scores"]["decision_making_visibility"]
        assert entry["passing"] is False
        assert entry["not_collected"] is True

    def test_below_threshold_under_documents_gap_is_not_collected(self, collector):
        s = collector._calculate_score([], {"found": [], "not_collected": ["Roadmap"]})
        entry = s["sub_scores"]["decision_making_visibility"]
        assert entry["not_collected"] is True

    def test_threshold_already_met_survives_a_gap(self, collector):
        s = collector._calculate_score(
            ["Wiki", "GitHub Discussions"], {"found": []}, channels_gap=True,
        )
        entry = s["sub_scores"]["decision_making_visibility"]
        assert entry["passing"] is True
        assert "not_collected" not in entry

    def test_max_score_zero_when_only_scorable_row_is_gapped(self, collector):
        s = collector._calculate_score([], {"found": [], "not_collected": ["Roadmap"]})
        assert s["score"] is None
        assert s["max_score"] == 0
        assert s["status"] == "not_collected"


class TestGetPublicChannelsGapHandling:
    def test_gap_is_tracked(self, collector):
        async def go():
            with patch.object(collector, "_github_get", new=AsyncMock(return_value=COLLECTION_GAP)):
                return await collector._get_public_channels(None, "o", "r")

        channels, saw_gap = asyncio.run(go())
        assert channels == []
        assert saw_gap is True

    def test_confirmed_flags_are_not_a_gap(self, collector):
        async def go():
            data = {"has_discussions": True, "has_wiki": False, "has_pages": False}
            with patch.object(collector, "_github_get", new=AsyncMock(return_value=data)):
                return await collector._get_public_channels(None, "o", "r")

        channels, saw_gap = asyncio.run(go())
        assert channels == ["GitHub Discussions"]
        assert saw_gap is False


class TestFindDecisionDocumentsGapHandling:
    def _run(self, collector, responses):
        async def fake_exists(client, owner, repo, path):
            return responses.get(path, None)

        async def go():
            with patch.object(collector, "_check_file_exists", side_effect=fake_exists):
                return await collector._find_decision_documents(None, "o", "r")

        return asyncio.run(go())

    def test_gapped_label_with_no_find_is_not_collected(self, collector):
        responses = {p: COLLECTION_GAP for paths in _DECISION_PATHS.values() for p in paths}
        result = self._run(collector, responses)
        assert result["found"] == []
        assert set(result["not_collected"]) == set(_DECISION_PATHS)

    def test_found_label_survives_gaps_on_others(self, collector):
        responses = {p: COLLECTION_GAP for paths in _DECISION_PATHS.values() for p in paths}
        responses["ROADMAP.md"] = "http://x"
        result = self._run(collector, responses)
        assert "Roadmap" in result["found"]
