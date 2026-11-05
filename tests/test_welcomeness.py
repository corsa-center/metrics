"""Unit tests for WelcomenessCollector (CASS Section 4.2.6)."""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from collectors.ecosystem.base import COLLECTION_GAP, RepoTree
from collectors.ecosystem.welcomeness import (
    WelcomenessCollector, _DECISION_PATHS, _DECISION_PATTERNS,
)
from tests.fakes import FakeForge
from forge.github import GitHubForge


@pytest.fixture
def collector():
    return WelcomenessCollector(GitHubForge())


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
            with patch.object(collector.forge, "_github_get", new=AsyncMock(return_value=COLLECTION_GAP)):
                return await collector._get_public_channels(None, "o/r")

        channels, saw_gap, _ = asyncio.run(go())
        assert channels == []
        assert saw_gap is True

    def test_confirmed_flags_are_not_a_gap(self, collector):
        async def go():
            data = {"has_discussions": True, "has_wiki": False, "has_pages": False}
            with patch.object(collector.forge, "_github_get", new=AsyncMock(side_effect=[data, None])):
                return await collector._get_public_channels(None, "o/r")

        channels, saw_gap, _ = asyncio.run(go())
        assert channels == ["GitHub Discussions"]
        assert saw_gap is False

    def _channels(self, collector, repo_flags, readme, wiki_pages):
        import base64
        readme_data = {"content": base64.b64encode(readme.encode()).decode()} if readme else None

        async def go():
            with patch.object(collector.forge, "_github_get", new=AsyncMock(side_effect=[repo_flags, readme_data])), \
                 patch.object(collector.forge, "wiki_has_content", new=AsyncMock(return_value=wiki_pages)):
                return await collector._get_public_channels(None, "o/r")
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
        tree = RepoTree(FakeForge(), "o/r", ["docs/Devicelib_roadmap.md"], truncated=False)
        result = collector._find_decision_documents(tree)
        assert "Roadmap" in result["found"]

    def test_meeting_notes_found_at_an_unenumerated_name(self, collector):
        # llvm/llvm-project's flang subproject -- MeetingNotes/, not
        # "meetings".
        tree = RepoTree(
            FakeForge(), "o/r", ["flang/docs/MeetingNotes/2025/2025-12-03.md"], truncated=False
        )
        result = collector._find_decision_documents(tree)
        assert "Meeting notes" in result["found"]

    def test_governance_document_found(self, collector):
        tree = RepoTree(FakeForge(), "o/r", ["GOVERNANCE.md"], truncated=False)
        result = collector._find_decision_documents(tree)
        assert "Governance document" in result["found"]

    def test_confirmed_absence_is_not_collected_free(self, collector):
        tree = RepoTree(FakeForge(), "o/r", ["README.md"], truncated=False)
        result = collector._find_decision_documents(tree)
        assert result["found"] == []
        assert result["not_collected"] == []

    def test_source_file_mentioning_roadmap_is_not_a_false_positive(self, collector):
        tree = RepoTree(FakeForge(), "o/r", ["src/Roadmapper.cpp"], truncated=False)
        result = collector._find_decision_documents(tree)
        assert "Roadmap" not in result["found"]


class TestEvidence:
    """What the report shows under Decision-Making Visibility (#99)."""

    @pytest.fixture
    def fake(self):
        return WelcomenessCollector(FakeForge())

    def _collect(self, collector, paths, readme):
        tree = RepoTree(collector.forge, "o/r", paths, truncated=False)
        collector.forge.readme_text = readme
        with patch.object(RepoTree, "fetch", new=AsyncMock(return_value=tree)):
            result = asyncio.run(collector.collect({"name": "r", "repo_url": "https://github.com/o/r"}))
        return result["overall_score"]["sub_scores"]["decision_making_visibility"]["evidence"]

    def test_readme_channel_keeps_the_text_that_matched(self, fake):
        fake.forge.repo_info_data = {"has_wiki": True}
        ev = self._collect(fake, ["README.md", "docs/roadmap.md"],
                           "# Proj\n\nQuestions go to the proj-users mailing list at lists.example.org.\n")
        assert {"text": "Mailing list", "path": "README.md",
                "url": "https://github.com/o/r/blob/HEAD/README.md",
                "quote": "Questions go to the proj-users mailing list at lists.example.org."} in ev["found"]
        assert {"text": "Roadmap", "path": "docs/roadmap.md",
                "url": "https://github.com/o/r/blob/HEAD/docs/roadmap.md"} in ev["found"]
        missing = [m["text"] for m in ev["missing"]]
        assert "GitHub Discussions (not enabled)" in missing
        assert "Wiki with pages (the wiki is enabled but has none)" in missing
        assert any(m.startswith("Forum in README.md (looked for: forum") for m in missing)
        assert "Roadmap" not in missing and "Meeting notes" in missing
        meeting = next(s for s in ev["searched"] if s["text"].startswith("Meeting notes"))
        assert meeting["paths"][0] == {"path": "a meeting-notes directory"}

    def test_missing_readme_is_named(self, fake):
        ev = self._collect(fake, [], None)
        assert {"text": "a README to read for mailing list, forum and chat links"} in ev["missing"]

    def test_gapped_lookups_claim_nothing(self, fake):
        fake.forge.gaps = {"repo_info"}
        with patch.object(RepoTree, "fetch", new=AsyncMock(return_value=COLLECTION_GAP)):
            result = asyncio.run(fake.collect({"name": "r", "repo_url": "https://github.com/o/r"}))
        ev = result["overall_score"]["sub_scores"]["decision_making_visibility"]["evidence"]
        assert ev == {"found": [], "missing": [], "searched": []}

    def test_gapped_readme_claims_no_readme_search(self, fake):
        fake.forge.gaps = {"readme"}
        ev = self._collect(fake, [], None)
        assert not any("README" in m["text"] for m in ev["missing"])

    def test_gitlab_has_no_discussions_to_miss(self):
        from tests.fakes import gitlab_fake
        ev = WelcomenessCollector(gitlab_fake())._visibility_evidence(
            [], {"details": {}}, {"settings": True})
        assert [m["text"] for m in ev["missing"]] == ["Wiki (not enabled)", "GitLab Pages (not enabled)"]

    def test_report_shows_it_under_the_row(self, fake):
        from orchestrator import MetricsOrchestrator
        from package_report import render_package_report
        tree = RepoTree(fake.forge, "o/r", ["GOVERNANCE.md"], truncated=False)
        documents = fake._find_decision_documents(tree)
        looked = {"settings": True, "readme": "read", "readme_matches": {
            "Chat (Slack/Discord/Matrix)": "Join us on <Slack>"},
            "readme_source": {"path": "README.md", "url": "https://github.com/o/r/blob/HEAD/README.md"}}
        welcomeness = {"overall_score": fake._calculate_score(
            ["Chat (Slack/Discord/Matrix)"], documents, looked=looked)}
        metrics = {"dimensions": {
            "impact": {"metadata": {"status": "placeholder"}},
            "ecosystem": {"sub_results": {"welcomeness": welcomeness}},
            "quality": {"sub_results": {}},
        }}
        orch = MetricsOrchestrator(config_path="config/orchestrator.yaml")
        dashboard = orch._transform_for_dashboard(metrics, {})
        page = render_package_report("o/r", dashboard, metrics, orch._metric_weights())

        row = page[page.index("<b>Decision-Making Visibility:</b>"):]
        row = row[:row.index("</li>")]
        assert ('<b>Found:</b> Chat (Slack/Discord/Matrix) in <a href="https://github.com/o/r/blob/HEAD/'
                'README.md">README.md</a>: “Join us on &lt;Slack&gt;”') in row
        assert ('<b>Found:</b> Governance document in <a href="https://github.com/o/r/blob/HEAD/'
                'GOVERNANCE.md">GOVERNANCE.md</a>') in row
        assert "<b>Not found:</b> Decision records</div>" in row
        assert "<b>Decision records looked for (paths in any capitalization):</b> docs/adr, adr" in row
