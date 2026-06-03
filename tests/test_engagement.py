"""Unit tests for EngagementCollector."""

import asyncio
import pytest
from collectors.sustainability.engagement import EngagementCollector, _is_bot, _hours, _parse_dt


@pytest.fixture
def collector():
    return EngagementCollector()


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

class TestHelpers:
    def test_is_bot_github_actions(self):
        assert _is_bot("github-actions[bot]") is True

    def test_is_bot_renovate(self):
        assert _is_bot("renovate-bot") is True

    def test_is_bot_human(self):
        assert _is_bot("octocat") is False

    def test_parse_dt_z_suffix(self):
        dt = _parse_dt("2024-01-15T10:00:00Z")
        assert dt is not None
        assert dt.year == 2024

    def test_parse_dt_none(self):
        assert _parse_dt(None) is None

    def test_parse_dt_invalid(self):
        assert _parse_dt("not-a-date") is None

    def test_hours_calculation(self):
        a = _parse_dt("2024-01-01T00:00:00Z")
        b = _parse_dt("2024-01-01T06:00:00Z")
        assert _hours(a, b) == 6.0

    def test_hours_none_when_missing(self):
        assert _hours(None, _parse_dt("2024-01-01T00:00:00Z")) is None


# ------------------------------------------------------------------ #
# Issue stats                                                          #
# ------------------------------------------------------------------ #

class TestComputeIssueStats:
    def _make_issue(self, created, closed=None):
        return {"created_at": created, "closed_at": closed, "number": 1, "comments": 0}

    def test_median_close_time(self, collector):
        issues = [
            self._make_issue("2024-01-01T00:00:00Z", "2024-01-03T00:00:00Z"),  # 48h
            self._make_issue("2024-01-01T00:00:00Z", "2024-01-05T00:00:00Z"),  # 96h
        ]
        result = collector._compute_issue_stats(issues, [None, None])
        assert result["median_close_time_hours"] == 72.0

    def test_response_times_included(self, collector):
        issues = [self._make_issue("2024-01-01T00:00:00Z")]
        result = collector._compute_issue_stats(issues, [12.0])
        assert result["median_first_response_hours"] == 12.0

    def test_none_responses_excluded(self, collector):
        issues = [self._make_issue("2024-01-01T00:00:00Z")] * 3
        result = collector._compute_issue_stats(issues, [None, None, None])
        assert result["median_first_response_hours"] is None

    def test_pct_with_response(self, collector):
        issues = [self._make_issue("2024-01-01T00:00:00Z")] * 4
        result = collector._compute_issue_stats(issues, [5.0, None, 10.0, None])
        assert result["pct_with_response"] == 50.0


# ------------------------------------------------------------------ #
# PR stats                                                             #
# ------------------------------------------------------------------ #

class TestComputePrStats:
    def _make_pr(self, created, merged_at=None):
        return {"created_at": created, "merged_at": merged_at}

    def test_merge_rate(self, collector):
        prs = [
            self._make_pr("2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z"),
            self._make_pr("2024-01-01T00:00:00Z", None),
        ]
        result = collector._compute_pr_stats(prs)
        assert result["merge_rate_pct"] == 50.0
        assert result["merged"] == 1
        assert result["closed_without_merge"] == 1

    def test_all_merged(self, collector):
        prs = [self._make_pr("2024-01-01T00:00:00Z", "2024-01-03T00:00:00Z")] * 3
        result = collector._compute_pr_stats(prs)
        assert result["merge_rate_pct"] == 100.0

    def test_empty_prs(self, collector):
        result = collector._compute_pr_stats([])
        assert result["merge_rate_pct"] is None
        assert result["median_cycle_time_hours"] is None

    def test_cycle_time(self, collector):
        prs = [self._make_pr("2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z")]  # 24h
        result = collector._compute_pr_stats(prs)
        assert result["median_cycle_time_hours"] == 24.0


# ------------------------------------------------------------------ #
# Scoring                                                              #
# ------------------------------------------------------------------ #

class TestScore:
    def _score(self, collector, frt=None, mct=None, mrp=None, ratio=None):
        return collector._score(
            {"median_first_response_hours": frt, "median_close_time_hours": mct},
            {"merge_rate_pct": mrp},
            {"sample_open_to_closed_ratio": ratio},
        )

    def test_perfect_collected_score(self, collector):
        # All 4 collected sub-metrics passing → 4/7
        result = self._score(collector, frt=1.0, mct=24.0, mrp=80.0, ratio=0.2)
        assert result["score"] == 4
        assert result["max_score"] == 7

    def test_zero_score_when_all_none(self, collector):
        result = self._score(collector)
        assert result["score"] == 0

    def test_fast_response_passes(self, collector):
        # frt < 168 h → 1 pt
        result = self._score(collector, frt=1.0)
        assert result["score"] == 1
        assert result["sub_scores"]["response_time_tracking"]["passing"] is True

    def test_slow_response_fails(self, collector):
        # frt >= 168 h → 0 pt
        result = self._score(collector, frt=200.0)
        assert result["sub_scores"]["response_time_tracking"]["passing"] is False

    def test_high_merge_rate_passes(self, collector):
        # mrp > 50 % → 1 pt
        result = self._score(collector, mrp=80.0)
        assert result["score"] == 1
        assert result["sub_scores"]["pr_flow"]["passing"] is True

    def test_low_backlog_passes(self, collector):
        # ratio < 2.0 → 1 pt
        result = self._score(collector, ratio=0.3)
        assert result["score"] == 1
        assert result["sub_scores"]["support_closure"]["passing"] is True

    def test_uncollected_sub_metrics_flagged(self, collector):
        result = self._score(collector)
        for key in ["engagement_quality", "communication_patterns", "community_participation"]:
            assert result["sub_scores"][key]["not_collected"] is True
            assert result["sub_scores"][key]["pts"] == 0


# ------------------------------------------------------------------ #
# collect() with invalid URL                                           #
# ------------------------------------------------------------------ #

class TestCollectInvalidUrl:
    def test_returns_empty(self, collector):
        result = asyncio.run(
            collector.collect({"name": "Bad", "repo_url": "not-a-url"})
        )
        assert result["repository"] == "unknown"
        assert result["overall_score"]["score"] == 0
