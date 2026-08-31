"""Unit tests for DevToolingCollector (CASS Section 4.3.2)."""

import pytest

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
