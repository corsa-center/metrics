"""Unit tests for DevToolingCollector (CASS Section 4.3.2)."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from collectors.ecosystem.base import COLLECTION_GAP, RepoTree
from collectors.quality.development_practices.dev_tooling import (
    DevToolingCollector, _TESTING_PATHS, _TOOLING_PATHS,
)


@pytest.fixture
def collector():
    return DevToolingCollector()


def _scan(found):
    return {"found": list(found), "missing": [], "details": {}}


class TestScoring:
    def _score(self, collector, testing=(), tooling=(), review=None):
        return collector._calculate_score(
            _scan(testing), _scan(tooling),
            review if review is not None else {"sampled": 0, "reviewed": 0, "coverage_pct": None},
        )

    def test_testing_threshold(self, collector):
        assert not self._score(collector, testing=["a"])["sub_scores"][
            "testing_framework"]["passing"]
        assert self._score(collector, testing=["a", "b"])["sub_scores"][
            "testing_framework"]["passing"]

    def test_tooling_threshold(self, collector):
        assert not self._score(collector, tooling=["a"])["sub_scores"][
            "dev_tool_integration"]["passing"]
        assert self._score(collector, tooling=["a", "b"])["sub_scores"][
            "dev_tool_integration"]["passing"]

    def test_review_coverage_threshold(self, collector):
        at = {"sampled": 10, "reviewed": 7, "coverage_pct": 70.0}
        below = {"sampled": 10, "reviewed": 6, "coverage_pct": 60.0}
        assert self._score(collector, review=at)["sub_scores"][
            "code_review_quality"]["passing"]
        assert not self._score(collector, review=below)["sub_scores"][
            "code_review_quality"]["passing"]

    def test_no_merged_prs_does_not_pass(self, collector):
        s = self._score(collector)["sub_scores"]["code_review_quality"]
        assert not s["passing"]
        assert "No merged PRs" in s["value"]

    def test_max_score_is_three(self, collector):
        assert self._score(collector)["max_score"] == 3

    def test_all_passing(self, collector):
        s = self._score(
            collector,
            testing=["a", "b"], tooling=["a", "b"],
            review={"sampled": 20, "reviewed": 20, "coverage_pct": 100.0},
        )
        assert s["score"] == 3
        assert s["percentage"] == 100.0

    def test_found_items_appear_as_detail(self, collector):
        s = self._score(collector, testing=["Test suite directory", "CTest / CMake testing"])
        assert s["sub_scores"]["testing_framework"]["detail"] == (
            "Test suite directory, CTest / CMake testing"
        )


class TestPathGroups:
    def test_group_labels_are_distinct(self):
        assert not set(_TESTING_PATHS) & set(_TOOLING_PATHS)

    def test_every_group_has_candidates(self):
        for group in (_TESTING_PATHS, _TOOLING_PATHS):
            for label, paths in group.items():
                assert paths, label


class TestEmptyResult:
    def test_invalid_url(self, collector):
        import asyncio
        r = asyncio.run(collector.collect({"name": "x", "repo_url": "nope"}))
        assert r["overall_score"]["max_score"] == 3
        assert r["code_review"]["coverage_pct"] is None


class TestScoringGapHandling:
    def test_below_threshold_under_gap_is_not_collected_not_a_negative(self, collector):
        testing = {"found": ["a"], "missing": [], "not_collected": ["b"], "details": {}}
        s = collector._calculate_score(testing, _scan([]), {"sampled": 0, "reviewed": 0, "coverage_pct": None})
        entry = s["sub_scores"]["testing_framework"]
        assert entry["passing"] is False
        assert entry["not_collected"] is True

    def test_threshold_already_met_survives_a_gap(self, collector):
        testing = {"found": ["a", "b"], "missing": [], "not_collected": ["c"], "details": {}}
        s = collector._calculate_score(testing, _scan([]), {"sampled": 0, "reviewed": 0, "coverage_pct": None})
        entry = s["sub_scores"]["testing_framework"]
        assert entry["passing"] is True
        assert "not_collected" not in entry

    def test_review_not_collected_is_excluded_not_scored_as_failure(self, collector):
        review = {"sampled": 0, "reviewed": 0, "coverage_pct": None, "not_collected": True}
        testing = _scan(["a", "b"])
        tooling = _scan(["a", "b"])
        s = collector._calculate_score(testing, tooling, review)
        # If not_collected silently counted as a failure this would be 2/3.
        assert s["score"] == 2
        assert s["max_score"] == 2
        assert s["percentage"] == 100.0

    def test_everything_gapped_reports_not_collected_status(self, collector):
        testing = {"found": [], "missing": [], "not_collected": ["a", "b"], "details": {}}
        tooling = {"found": [], "missing": [], "not_collected": ["a", "b"], "details": {}}
        review = {"sampled": 0, "reviewed": 0, "coverage_pct": None, "not_collected": True}
        s = collector._calculate_score(testing, tooling, review)
        assert s["score"] is None
        assert s["max_score"] == 0
        assert s["status"] == "not_collected"


class TestScan:
    """_scan now takes a RepoTree (or COLLECTION_GAP) directly -- see
    METRIC_BLIND_SPOTS.md class F1.
    """

    def test_gapped_tree_reports_every_item_not_collected(self, collector):
        result = collector._scan(COLLECTION_GAP, _TESTING_PATHS)
        assert result["found"] == []
        assert set(result["not_collected"]) == set(_TESTING_PATHS)

    def test_found_group_survives_confirmed_misses_on_other_groups(self, collector):
        tree = RepoTree("o", "r", ["pytest.ini"], truncated=False)
        result = collector._scan(tree, _TESTING_PATHS)
        assert "pytest configuration" in result["found"]
        assert result["not_collected"] == []

    def test_capitalized_tests_directory_is_found(self, collector):
        # AMReX-Codes/amrex's test directory is "Tests" (capitalized), which
        # a lowercase-only literal comparison never matches even though a
        # real test suite is right there.
        tree = RepoTree("o", "r", ["Tests/CMakeLists.txt"], truncated=False)
        result = collector._scan(tree, _TESTING_PATHS)
        assert "Test suite directory" in result["found"]

    def test_vendored_test_framework_directory_is_found(self, collector):
        tree = RepoTree("o", "r", ["test/googletest/README.md"], truncated=False)
        result = collector._scan(tree, _TESTING_PATHS)
        assert "Test framework vendored" in result["found"]


class TestAnalyzeReviewCoverageGapHandling:
    def _run(self, collector, github_get_side_effect):
        async def go():
            with patch.object(collector, "_github_get", side_effect=github_get_side_effect):
                return await collector._analyze_review_coverage(None, "o", "r")

        return asyncio.run(go())

    def test_pr_listing_gap_is_not_collected(self, collector):
        async def fake(client, url, params=None):
            return COLLECTION_GAP

        result = self._run(collector, fake)
        assert result["not_collected"] is True
        assert result["coverage_pct"] is None

    def test_confirmed_no_merged_prs_is_a_real_negative(self, collector):
        async def fake(client, url, params=None):
            return []

        result = self._run(collector, fake)
        assert result["coverage_pct"] is None
        assert "not_collected" not in result

    def test_review_check_gap_excludes_pr_from_denominator(self, collector):
        prs = [
            {"number": 1, "merged_at": "2024-01-01T00:00:00Z"},
            {"number": 2, "merged_at": "2024-01-02T00:00:00Z"},
        ]

        async def fake(client, url, params=None):
            if "/pulls?" in url:
                return prs
            if "/1/reviews" in url:
                return [{"id": 1}]
            if "/2/reviews" in url:
                return COLLECTION_GAP
            return None

        result = self._run(collector, fake)
        # PR 2's review check gapped, so it's dropped from the denominator
        # rather than silently counted as unreviewed.
        assert result["sampled"] == 1
        assert result["reviewed"] == 1
        assert result["coverage_pct"] == 100.0

    def test_all_review_checks_gapped_reports_not_collected(self, collector):
        prs = [{"number": 1, "merged_at": "2024-01-01T00:00:00Z"}]

        async def fake(client, url, params=None):
            if "/pulls?" in url:
                return prs
            return COLLECTION_GAP

        result = self._run(collector, fake)
        assert result["not_collected"] is True
