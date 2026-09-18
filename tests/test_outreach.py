"""Unit tests for OutreachCollector (CASS Section 4.2.5)."""

import asyncio
import pytest

from forge.base import COLLECTION_GAP
from collectors.ecosystem.outreach import OutreachCollector, _ONBOARDING_PATHS


class FakeForge:
    """Minimal stand-in for GitHubForge/GitLabForge."""

    def __init__(self):
        self.contributors_pages = []
        self.commits_pages = []
        self.search_results = {}
        self.file_results = {}

    def extract_ref(self, repo_url):
        return None if repo_url == "not-a-url" else "o/r"

    async def contributors(self, client, ref, *, per_page=100, page=1):
        idx = page - 1
        return self.contributors_pages[idx] if idx < len(self.contributors_pages) else []

    async def commits(self, client, ref, *, since=None, per_page=100, page=1):
        idx = page - 1
        return self.commits_pages[idx] if idx < len(self.commits_pages) else []

    async def search_issues(self, client, query, *, per_page=1):
        for key, value in self.search_results.items():
            if key in query:
                return value
        return 0

    async def file_exists(self, client, ref, path):
        return self.file_results.get(path)

    def get_timestamp(self):
        return "2026-01-01T00:00:00+00:00"


@pytest.fixture
def forge():
    return FakeForge()


@pytest.fixture
def collector(forge):
    return OutreachCollector(forge)


class TestContributorGrowth:
    def test_new_contributor_is_one_with_no_prior_history(self, collector):
        # alice has 3 all-time and 3 recent → entirely new.
        # bob has 50 all-time but only 2 recent → an existing contributor.
        contributors = [{"identity": "alice", "commit_count": 3},
                        {"identity": "bob", "commit_count": 50}]
        recent = {"alice": 3, "bob": 2}
        g = collector._analyze_contributor_growth(contributors, recent)
        assert g["new_contributors"] == 1

    def test_retention_counts_newcomers_who_came_back(self, collector):
        contributors = [{"identity": "a", "commit_count": 1},
                        {"identity": "b", "commit_count": 4},
                        {"identity": "c", "commit_count": 1}]
        recent = {"a": 1, "b": 4, "c": 1}
        g = collector._analyze_contributor_growth(contributors, recent)
        assert g["new_contributors"] == 3
        assert g["retained_new_contributors"] == 1   # only b has >= 2
        assert g["retention_rate"] == pytest.approx(33.3)

    def test_retention_is_none_without_newcomers(self, collector):
        contributors = [{"identity": "bob", "commit_count": 50}]
        g = collector._analyze_contributor_growth(contributors, {"bob": 2})
        assert g["new_contributors"] == 0
        assert g["retention_rate"] is None

    def test_lifecycle_buckets(self, collector):
        contributors = [
            {"identity": "one", "commit_count": 1},
            {"identity": "two", "commit_count": 4},
            {"identity": "three", "commit_count": 5},
            {"identity": "four", "commit_count": 900},
        ]
        g = collector._analyze_contributor_growth(contributors, {})
        assert g["lifecycle"] == {"one_time": 1, "casual": 1, "repeat": 2}

    def test_no_contributors(self, collector):
        g = collector._analyze_contributor_growth([], {})
        assert g["total_contributors"] == 0
        assert g["retention_rate"] is None

    def test_recent_author_absent_from_contributors_is_ignored(self, collector):
        # /contributors is capped at 5 pages; an author beyond it must not be
        # miscounted as new just because their total is unknown.
        g = collector._analyze_contributor_growth(
            [{"identity": "known", "commit_count": 10}], {"unknown": 3}
        )
        assert g["new_contributors"] == 0


class TestScoring:
    def _score(self, collector, growth=None, issues=None, onboarding=None):
        return collector._calculate_score(
            growth or {}, issues or {}, onboarding or {"found": []}
        )

    def test_three_submetrics_stay_uncollected(self, collector):
        sub = self._score(collector)["sub_scores"]
        uncollected = [k for k, v in sub.items() if v.get("not_collected")]
        assert len(uncollected) == 3
        # The 3 permanently-uncollected submetrics must not inflate the
        # denominator -- only the 5 actually-measured ones are scorable.
        assert self._score(collector)["max_score"] == 5

    def test_retention_threshold(self, collector):
        assert self._score(collector, {"retention_rate": 50})["sub_scores"][
            "contributor_retention"]["passing"]
        assert not self._score(collector, {"retention_rate": 49})["sub_scores"][
            "contributor_retention"]["passing"]

    def test_good_first_issue_needs_open_ones(self, collector):
        # Closed-only history doesn't help a newcomer arriving today.
        s = self._score(collector, issues={"total": 5, "open": 0, "closed": 5})
        assert not s["sub_scores"]["good_first_issue"]["passing"]
        s = self._score(collector, issues={"total": 5, "open": 2, "closed": 3})
        assert s["sub_scores"]["good_first_issue"]["passing"]

    def test_onboarding_threshold(self, collector):
        assert not self._score(collector, onboarding={"found": ["a", "b"]})[
            "sub_scores"]["onboarding_infrastructure"]["passing"]
        assert self._score(collector, onboarding={"found": ["a", "b", "c"]})[
            "sub_scores"]["onboarding_infrastructure"]["passing"]


class TestNewcomerLabelQuery:
    def test_all_labels_go_in_one_query(self):
        # Eight searches (four labels x two states) became two. Comma-separated
        # values in a label: qualifier are ORed, and the OR form deduplicates
        # issues carrying more than one of the labels.
        from collectors.ecosystem.outreach import _NEWCOMER_LABELS
        labels = ",".join(f'"{l}"' if " " in l else l for l in _NEWCOMER_LABELS)
        assert labels == '"good first issue","help wanted",good-first-issue,newcomer'

    def test_spaced_labels_are_quoted(self):
        from collectors.ecosystem.outreach import _NEWCOMER_LABELS
        labels = ",".join(f'"{l}"' if " " in l else l for l in _NEWCOMER_LABELS)
        assert '"good first issue"' in labels
        assert "good-first-issue" in labels and '"good-first-issue"' not in labels


class TestEmptyResult:
    def test_invalid_url(self, collector):
        r = asyncio.run(collector.collect({"name": "x", "repo_url": "not-a-url"}))
        assert r["overall_score"]["score"] == 0
        assert r["overall_score"]["max_score"] == 5


class TestScoringGapHandling:
    def _score(self, collector, growth=None, issues=None, onboarding=None,
               contributors_gap=False, commits_gap=False):
        return collector._calculate_score(
            growth or {}, issues or {}, onboarding or {"found": []},
            contributors_gap, commits_gap,
        )

    def test_no_new_contributors_under_gap_is_not_collected(self, collector):
        s = self._score(collector, contributors_gap=True)
        entry = s["sub_scores"]["new_contributor_tracking"]
        assert entry["passing"] is False
        assert entry["not_collected"] is True

    def test_new_contributors_found_survives_a_gap(self, collector):
        s = self._score(collector, growth={"new_contributors": 2}, contributors_gap=True)
        entry = s["sub_scores"]["new_contributor_tracking"]
        assert entry["passing"] is True
        assert "not_collected" not in entry

    def test_retention_under_commits_gap_is_not_collected(self, collector):
        s = self._score(collector, commits_gap=True)
        assert s["sub_scores"]["contributor_retention"]["not_collected"] is True

    def test_lifecycle_ignores_commits_gap(self, collector):
        # Lifecycle buckets are derived only from the contributors list, not
        # recent commits, so a commits-only gap doesn't taint it.
        s = self._score(collector, commits_gap=True)
        assert "not_collected" not in s["sub_scores"]["contributor_lifecycle"]

    def test_lifecycle_under_contributors_gap_is_not_collected(self, collector):
        s = self._score(collector, contributors_gap=True)
        assert s["sub_scores"]["contributor_lifecycle"]["not_collected"] is True

    def test_good_first_issue_under_gap_is_not_collected(self, collector):
        s = self._score(collector, issues={"total": 0, "open": 0, "closed": 0, "not_collected": True})
        assert s["sub_scores"]["good_first_issue"]["not_collected"] is True

    def test_onboarding_under_gap_is_not_collected(self, collector):
        s = self._score(collector, onboarding={"found": [], "not_collected": ["Issue templates"]})
        assert s["sub_scores"]["onboarding_infrastructure"]["not_collected"] is True

    def test_everything_gapped_reports_not_collected_status(self, collector):
        s = self._score(
            collector,
            issues={"total": 0, "open": 0, "closed": 0, "not_collected": True},
            onboarding={"found": [], "not_collected": list(_ONBOARDING_PATHS)},
            contributors_gap=True, commits_gap=True,
        )
        assert s["score"] is None
        assert s["max_score"] == 0
        assert s["status"] == "not_collected"


class TestGetContributorsGapHandling:
    def test_gap_on_first_page_reports_gap_with_partial_results(self, collector, forge):
        forge.contributors_pages = [COLLECTION_GAP]
        contributors, saw_gap = asyncio.run(collector._get_contributors(None, "o/r"))
        assert contributors == []
        assert saw_gap is True

    def test_gap_after_a_successful_first_page_keeps_what_was_fetched(self, collector, forge):
        forge.contributors_pages = [
            [{"identity": "a", "commit_count": 5}], COLLECTION_GAP,
        ]
        contributors, saw_gap = asyncio.run(collector._get_contributors(None, "o/r"))
        assert len(contributors) == 1
        assert saw_gap is True

    def test_clean_exhaustion_is_not_a_gap(self, collector, forge):
        forge.contributors_pages = [[]]
        contributors, saw_gap = asyncio.run(collector._get_contributors(None, "o/r"))
        assert contributors == []
        assert saw_gap is False


class TestGetNewcomerIssuesGapHandling:
    def test_open_search_failure_with_zero_is_not_collected(self, collector, forge):
        forge.search_results = {"state:open": None, "state:closed": 3}
        result = asyncio.run(collector._get_newcomer_issues(None, "o/r"))
        assert result["not_collected"] is True

    def test_open_confirmed_nonzero_survives_a_closed_gap(self, collector, forge):
        forge.search_results = {"state:open": 4, "state:closed": None}
        result = asyncio.run(collector._get_newcomer_issues(None, "o/r"))
        assert result["open"] == 4
        assert "not_collected" not in result


class TestCheckOnboardingGapHandling:
    def _run(self, collector, forge, responses):
        forge.file_results = responses
        return asyncio.run(collector._check_onboarding(None, "o/r"))

    def test_gapped_label_with_no_find_is_not_collected(self, collector, forge):
        responses = {p: COLLECTION_GAP for paths in _ONBOARDING_PATHS.values() for p in paths}
        result = self._run(collector, forge, responses)
        assert result["found"] == []
        assert set(result["not_collected"]) == set(_ONBOARDING_PATHS)

    def test_found_label_survives_gaps_on_others(self, collector, forge):
        responses = {p: COLLECTION_GAP for paths in _ONBOARDING_PATHS.values() for p in paths}
        responses["CONTRIBUTING.md"] = "http://x"
        result = self._run(collector, forge, responses)
        assert "Contributing guide" in result["found"]
