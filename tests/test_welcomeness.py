"""Unit tests for WelcomenessCollector (CASS Section 4.2.6)."""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from collectors.ecosystem.base import COLLECTION_GAP, RepoTree
from collectors.ecosystem.welcomeness import (
    WelcomenessCollector, _DECISION_PATHS, _DECISION_PATTERNS,
)


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
            with patch.object(collector, "_github_get", new=AsyncMock(side_effect=[data, None])):
                return await collector._get_public_channels(None, "o", "r")

        channels, saw_gap = asyncio.run(go())
        assert channels == ["GitHub Discussions"]
        assert saw_gap is False

    def _channels(self, collector, repo_flags, readme, wiki_pages):
        import base64
        readme_data = {"content": base64.b64encode(readme.encode()).decode()} if readme else None

        async def go():
            with patch.object(collector, "_github_get", new=AsyncMock(side_effect=[repo_flags, readme_data])), \
                 patch("collectors.ecosystem.welcomeness.wiki_has_content", new=AsyncMock(return_value=wiki_pages)):
                return await collector._get_public_channels(None, "o", "r")
        return asyncio.run(go())[0]

    def test_empty_wiki_is_not_a_channel(self, collector):
        assert self._channels(collector, {"has_wiki": True}, "", wiki_pages=False) == []

    def test_wiki_with_pages_is_a_channel(self, collector):
        assert self._channels(collector, {"has_wiki": True}, "", wiki_pages=True) == ["Wiki"]

    def test_readme_linked_mailing_list_counts(self, collector):
        # SUNDIALS: "SUNDIALS [mailing list](https://computing.llnl.gov/...)".
        out = self._channels(collector, {}, "Questions? Use the SUNDIALS mailing list.", wiki_pages=False)
        assert out == ["Mailing list"]

    def test_help_desk_is_not_a_decision_channel(self, collector):
        assert self._channels(collector, {}, "File a ticket with our help desk.", wiki_pages=False) == []


class TestFindDecisionDocuments:
    """_find_decision_documents now takes a RepoTree (or COLLECTION_GAP)
    directly -- see METRIC_BLIND_SPOTS.md class F1/F2. Roadmap and Meeting
    notes are matched by regex (_DECISION_PATTERNS), not a literal path list,
    since a project's roadmap doesn't have to be named exactly "roadmap.md".
    """

    _ALL_LABELS = set(_DECISION_PATHS) | set(_DECISION_PATTERNS)

    def test_gapped_tree_reports_every_label_not_collected(self, collector):
        result = collector._find_decision_documents(COLLECTION_GAP)
        assert result["found"] == []
        assert set(result["not_collected"]) == self._ALL_LABELS

    def test_roadmap_found_at_an_unenumerated_name(self, collector):
        # CHIP-SPV/chipStar's actual filename -- no literal candidate list
        # would have enumerated this spelling.
        tree = RepoTree("o", "r", ["docs/Devicelib_roadmap.md"], truncated=False)
        result = collector._find_decision_documents(tree)
        assert "Roadmap" in result["found"]

    def test_meeting_notes_found_at_an_unenumerated_name(self, collector):
        # llvm/llvm-project's flang subproject -- MeetingNotes/, not
        # "meetings".
        tree = RepoTree(
            "o", "r", ["flang/docs/MeetingNotes/2025/2025-12-03.md"], truncated=False
        )
        result = collector._find_decision_documents(tree)
        assert "Meeting notes" in result["found"]

    def test_governance_document_found(self, collector):
        tree = RepoTree("o", "r", ["GOVERNANCE.md"], truncated=False)
        result = collector._find_decision_documents(tree)
        assert "Governance document" in result["found"]

    def test_confirmed_absence_is_not_collected_free(self, collector):
        tree = RepoTree("o", "r", ["README.md"], truncated=False)
        result = collector._find_decision_documents(tree)
        assert result["found"] == []
        assert result["not_collected"] == []

    def test_source_file_mentioning_roadmap_is_not_a_false_positive(self, collector):
        tree = RepoTree("o", "r", ["src/Roadmapper.cpp"], truncated=False)
        result = collector._find_decision_documents(tree)
        assert "Roadmap" not in result["found"]
