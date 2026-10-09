"""Unit tests for OutreachCollector (CASS Section 4.2.5)."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from collectors.ecosystem.base import COLLECTION_GAP, RepoTree
from collectors.ecosystem.outreach import OutreachCollector, _ONBOARDING_LABELS
from tests.fakes import FakeForge


@pytest.fixture
def collector():
    return OutreachCollector(FakeForge())


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
        growth = {"new_contributors": 5, "retention_rate": 40.0}
        sub = self._score(collector, growth)["sub_scores"]
        uncollected = [k for k, v in sub.items() if v.get("not_collected")]
        assert len(uncollected) == 3
        # The 3 permanently-uncollected submetrics must not inflate the
        # denominator -- only the 5 actually-measured ones are scorable.
        assert self._score(collector, growth)["max_score"] == 5

    def test_retention_threshold(self, collector):
        assert self._score(collector, {"retention_rate": 50, "new_contributors": 4})["sub_scores"][
            "contributor_retention"]["passing"]
        assert not self._score(collector, {"retention_rate": 49, "new_contributors": 4})["sub_scores"][
            "contributor_retention"]["passing"]

    def test_retention_of_too_few_newcomers_is_unmarked(self, collector):
        for growth in ({"new_contributors": 0, "retention_rate": None},
                       {"new_contributors": 2, "retention_rate": 0.0}):
            s = self._score(collector, growth)
            assert s["sub_scores"]["contributor_retention"]["insufficient_sample"]
            assert s["max_score"] == 4

    def test_truncated_contributor_list_is_not_measurable(self, collector):
        contributors = [{"identity": f"core{i}", "commit_count": 500} for i in range(20)]
        recent = {**{f"core{i}": 50 for i in range(20)}, **{f"new{i}": 1 for i in range(30)}}
        growth = collector._analyze_contributor_growth(contributors, recent)
        assert growth["contributor_list_truncated"]
        sub = self._score(collector, growth)["sub_scores"]
        for key in ("new_contributor_tracking", "contributor_retention", "contributor_lifecycle"):
            assert sub[key].get("unmeasured"), key
            assert "Not measurable" in sub[key]["value"]

    def test_one_or_two_unlisted_authors_is_not_truncation(self, collector):
        # The contributor list lags new commits slightly; that isn't truncation.
        contributors = [{"identity": f"c{i}", "commit_count": 5} for i in range(10)]
        recent = {**{f"c{i}": 5 for i in range(10)}, "brand_new": 1}
        assert not collector._analyze_contributor_growth(contributors, recent)["contributor_list_truncated"]

    def test_capped_commit_window_leaves_new_contributors_unmeasured(self, collector):
        growth = {"new_contributors": 0, "retention_rate": None, "commit_window_truncated": True}
        sub = self._score(collector, growth)["sub_scores"]
        assert sub["new_contributor_tracking"].get("unmeasured")
        assert not sub["contributor_lifecycle"].get("unmeasured")

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
        # Comma-separated values in a label: qualifier are ORed, so all
        # labels take one search per state.
        from collectors.ecosystem.outreach import _NEWCOMER_LABELS, _label_query
        assert _label_query(_NEWCOMER_LABELS) == '"good first issue","help wanted",good-first-issue,newcomer'

    def test_spaced_and_namespaced_labels_are_quoted(self):
        from collectors.ecosystem.outreach import _label_query
        assert _label_query(["is:good-first-issue", "good-first-issue"]) == '"is:good-first-issue",good-first-issue'

    def test_repository_labels_in_their_own_naming_are_used(self, collector):
        labels = [{"name": n} for n in ["bug", "is:good-first-issue", "is:help-wanted",
                                          "difficulty: easy", "easy-to-review-ish", "reg:helper-scripts"]]
        collector.forge.label_list = labels
        got = asyncio.run(collector._newcomer_labels(None, "o/r"))
        assert got == ["is:good-first-issue", "is:help-wanted", "difficulty: easy"]

    def test_no_matching_labels_falls_back_to_common_names(self, collector):
        from collectors.ecosystem.outreach import _NEWCOMER_LABELS
        collector.forge.label_list = [{"name": "bug"}]
        assert asyncio.run(collector._newcomer_labels(None, "o/r")) == _NEWCOMER_LABELS
        collector.forge.gaps = {"labels"}
        assert asyncio.run(collector._newcomer_labels(None, "o/r")) == _NEWCOMER_LABELS



class TestEmptyResult:
    def test_invalid_url(self, collector):
        import asyncio
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
            onboarding={"found": [], "not_collected": list(_ONBOARDING_LABELS)},
            contributors_gap=True, commits_gap=True,
        )
        assert s["score"] is None
        assert s["max_score"] == 0
        assert s["status"] == "not_collected"


class TestGetContributorsGapHandling:
    def test_gap_on_first_page_reports_gap_with_partial_results(self, collector):
        collector.forge.gaps = {"contributors"}
        contributors, saw_gap = asyncio.run(collector._get_contributors(None, "o/r"))
        assert contributors == []
        assert saw_gap is True

    def test_gap_after_a_successful_first_page_keeps_what_was_fetched(self, collector):
        async def contributors(client, ref, *, per_page=100, page=1):
            if page == 1:
                return [{"identity": f"u{i}", "commit_count": 5} for i in range(100)]
            return COLLECTION_GAP
        collector.forge.contributors = contributors

        contributors_out, saw_gap = asyncio.run(collector._get_contributors(None, "o/r"))
        assert len(contributors_out) == 100
        assert saw_gap is True

    def test_clean_exhaustion_is_not_a_gap(self, collector):
        contributors, saw_gap = asyncio.run(collector._get_contributors(None, "o/r"))
        assert contributors == []
        assert saw_gap is False


class TestRecentCommitWindow:
    def _walk(self, collector, total_commits):
        collector.forge.commit_list = [{"author_identity": "a"}] * total_commits
        return asyncio.run(collector._get_recent_commit_authors(None, "o/r"))

    def test_hitting_the_page_cap_is_truncation(self, collector):
        from collectors.ecosystem.outreach import _MAX_COMMIT_PAGES
        counts, gap, truncated = self._walk(collector, _MAX_COMMIT_PAGES * 100 + 1)
        assert truncated and not gap

    def test_reaching_the_end_is_not_truncation(self, collector):
        _, _, truncated = self._walk(collector, 150)
        assert not truncated


class TestGetNewcomerIssuesGapHandling:
    def test_open_search_failure_with_zero_is_not_collected(self, collector):
        collector.forge.search = lambda q: None if "state:open" in q else 3
        result = asyncio.run(collector._get_newcomer_issues(None, "o/r"))
        assert result["not_collected"] is True

    def test_open_confirmed_nonzero_survives_a_closed_gap(self, collector):
        collector.forge.search = lambda q: 4 if "state:open" in q else None
        result = asyncio.run(collector._get_newcomer_issues(None, "o/r"))
        assert result["open"] == 4
        assert "not_collected" not in result


class TestCheckOnboarding:
    """_check_onboarding now takes a RepoTree (or COLLECTION_GAP) directly --
    see corsa-center/metrics#49 and METRIC_BLIND_SPOTS.md class F2.
    """

    def test_gap_tree_reports_all_labels_not_collected(self, collector):
        result = collector._check_onboarding(COLLECTION_GAP)
        assert result["found"] == []
        assert set(result["not_collected"]) == set(_ONBOARDING_LABELS)

    def test_contributing_guide_found(self, collector):
        tree = RepoTree(FakeForge(), "o/r", ["CONTRIBUTING.md"], truncated=False)
        result = collector._check_onboarding(tree)
        assert "Contributing guide" in result["found"]

    def test_issue_template_directory_counts(self, collector):
        tree = RepoTree(FakeForge(), "o/r", [".github/ISSUE_TEMPLATE/bug_report.md"], truncated=False)
        result = collector._check_onboarding(tree)
        assert "Issue templates" in result["found"]

    def test_getting_started_guide_found_at_an_unenumerated_path(self, collector):
        # AMReX-Codes/amrex's actual location -- none of the six literal
        # paths this check used to enumerate would have matched it.
        tree = RepoTree(
            FakeForge(), "o/r",
            ["Docs/sphinx_documentation/source/GettingStarted.rst"],
            truncated=False,
        )
        result = collector._check_onboarding(tree)
        assert "Getting-started guide" in result["found"]

    def test_missing_resources_are_confirmed_absent_not_gapped(self, collector):
        tree = RepoTree(FakeForge(), "o/r", ["README.md"], truncated=False)
        result = collector._check_onboarding(tree)
        assert result["found"] == []
        assert set(result["missing"]) == set(_ONBOARDING_LABELS)
        assert result["not_collected"] == []

    def test_contributing_guide_in_nested_docs_tree(self, collector):
        tree = RepoTree(FakeForge(), "o/r", ["src/docs/sphinx/developer/contributing.rst"], truncated=False)
        assert "Contributing guide" in collector._check_onboarding(tree)["found"]

    def test_bundled_subproject_contributing_guide_not_counted(self, collector):
        tree = RepoTree(FakeForge(), "o/r", ["packages/lib/docs/CONTRIBUTING.md"], truncated=False)
        assert "Contributing guide" in collector._check_onboarding(tree)["missing"]

    def test_top_level_tutorial_directory_counts(self, collector):
        tree = RepoTree(FakeForge(), "o/r", ["tutorial/00_hello/main.cc"], truncated=False)
        assert "Getting-started guide" in collector._check_onboarding(tree)["found"]


class TestReadmeOnboarding:
    def _missing_all(self, collector):
        return collector._check_onboarding(RepoTree(FakeForge(), "o/r", ["README.md"], truncated=False))

    def test_readme_getting_started_section_counts(self, collector):
        onboarding = self._missing_all(collector)
        collector._credit_readme(onboarding, "# Proj\n\n## Installing / Getting started\n\nmake\n")
        assert onboarding["found"] == ["Getting-started guide"]
        assert "Getting-started guide" not in onboarding["missing"]

    def test_readme_contributing_section_needs_a_process(self, collector):
        onboarding = self._missing_all(collector)
        collector._credit_readme(onboarding, "## Contributing\n\nWe welcome contributions!\n")
        assert "Contributing guide" in onboarding["missing"]
        collector._credit_readme(onboarding, "## Contributing\n\nFork the repo and open a pull request.\n")
        assert "Contributing guide" in onboarding["found"]

    def test_readme_is_fetched_only_when_something_is_missing(self, collector):
        tree = RepoTree(FakeForge(), "o/r", ["CONTRIBUTING.md", ".github/ISSUE_TEMPLATE.md",
                                   ".github/PULL_REQUEST_TEMPLATE.md", "docs/quickstart.md"],
                        truncated=False)
        with patch.object(collector, "_get_contributors", new=AsyncMock(return_value=([], False))), \
             patch.object(collector, "_get_recent_commit_authors", new=AsyncMock(return_value=({}, False, False))), \
             patch.object(collector, "_get_newcomer_issues", new=AsyncMock(return_value={"open": 0, "closed": 0, "total": 0})), \
             patch.object(RepoTree, "fetch", new=AsyncMock(return_value=tree)), \
             patch.object(collector.forge, "readme", new=AsyncMock(return_value="")) as readme:
            result = asyncio.run(collector.collect({"name": "r", "repo_url": "https://github.com/o/r"}))
        readme.assert_not_called()
        assert len(result["onboarding"]["found"]) == 4
