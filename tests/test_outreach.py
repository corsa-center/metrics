"""Unit tests for OutreachCollector (CASS Section 4.2.5)."""

import pytest

from collectors.sustainability.outreach import OutreachCollector


@pytest.fixture
def collector():
    return OutreachCollector()


class TestContributorGrowth:
    def test_new_contributor_is_one_with_no_prior_history(self, collector):
        # alice has 3 all-time and 3 recent → entirely new.
        # bob has 50 all-time but only 2 recent → an existing contributor.
        contributors = [{"login": "alice", "contributions": 3},
                        {"login": "bob", "contributions": 50}]
        recent = {"alice": 3, "bob": 2}
        g = collector._analyze_contributor_growth(contributors, recent)
        assert g["new_contributors"] == 1

    def test_retention_counts_newcomers_who_came_back(self, collector):
        contributors = [{"login": "a", "contributions": 1},
                        {"login": "b", "contributions": 4},
                        {"login": "c", "contributions": 1}]
        recent = {"a": 1, "b": 4, "c": 1}
        g = collector._analyze_contributor_growth(contributors, recent)
        assert g["new_contributors"] == 3
        assert g["retained_new_contributors"] == 1   # only b has >= 2
        assert g["retention_rate"] == pytest.approx(33.3)

    def test_retention_is_none_without_newcomers(self, collector):
        contributors = [{"login": "bob", "contributions": 50}]
        g = collector._analyze_contributor_growth(contributors, {"bob": 2})
        assert g["new_contributors"] == 0
        assert g["retention_rate"] is None

    def test_lifecycle_buckets(self, collector):
        contributors = [
            {"login": "one", "contributions": 1},
            {"login": "two", "contributions": 4},
            {"login": "three", "contributions": 5},
            {"login": "four", "contributions": 900},
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
            [{"login": "known", "contributions": 10}], {"unknown": 3}
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
        assert self._score(collector)["max_score"] == 8

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


class TestPagination:
    def test_next_link_parsing(self, collector):
        header = ('<https://api.github.com/x?page=2>; rel="next", '
                  '<https://api.github.com/x?page=9>; rel="last"')
        assert collector._next_link(header).endswith("page=2")
        assert collector._next_link(None) is None


class TestEmptyResult:
    def test_invalid_url(self, collector):
        import asyncio
        r = asyncio.run(collector.collect({"name": "x", "repo_url": "not-a-url"}))
        assert r["overall_score"]["score"] == 0
        assert r["overall_score"]["max_score"] == 8
