"""Unit tests for EngagementCollector."""

import asyncio
import pytest
from collectors.ecosystem.engagement import EngagementCollector, _hours, _parse_dt
from tests.fakes import FakeForge


@pytest.fixture
def collector():
    return EngagementCollector(FakeForge())


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

class TestHelpers:
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

# Issue stats from a real sample, for tests about the other rows.
_SAMPLED = {"sample_size": 30, "discussion_sample_size": 30}


class TestScore:
    def _score(self, collector, frt=None, mct=None, mrp=None, ratio=None):
        flow = {}
        if mct is not None:
            flow.update(cohort_size=10, cohort_still_open=0, median_close_hours=mct)
        if ratio is not None:
            flow.update(opened=round(ratio * 100), closed=100)
        return collector._score(
            {"median_first_response_hours": frt, "sample_size": 30, "discussion_sample_size": 30},
            {"merge_rate_pct": mrp},
            {},
            flow,
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

    def test_all_seven_sub_metrics_are_collected(self, collector):
        # These three were previously flagged not_collected.
        result = self._score(collector)
        for key in ["engagement_quality", "communication_patterns",
                    "community_participation"]:
            assert not result["sub_scores"][key].get("not_collected")


class TestNewSubMetrics:
    """Engagement quality, communication patterns, community participation."""

    def _score(self, collector, **issue):
        base = {"median_first_response_hours": None, "median_close_time_hours": None,
                "sample_size": 30, "discussion_sample_size": 30, "median_comments": None,
                "timely_response_share": None, "outside_authors": 0}
        base.update(issue)
        pr = {"merge_rate_pct": None, "sample_size": 30,
              "outside_authors": issue.pop("pr_outside", 0)}
        return collector._score(base, pr, {"sample_open_to_closed_ratio": None})

    def test_comment_depth_threshold(self, collector):
        assert self._score(collector, median_comments=2)["sub_scores"][
            "engagement_quality"]["passing"]
        assert not self._score(collector, median_comments=1)["sub_scores"][
            "engagement_quality"]["passing"]

    def test_timely_response_threshold(self, collector):
        assert self._score(collector, timely_response_share=0.70)["sub_scores"][
            "communication_patterns"]["passing"]
        assert not self._score(collector, timely_response_share=0.69)["sub_scores"][
            "communication_patterns"]["passing"]

    def test_timely_response_wording(self, collector):
        info = self._score(collector, timely_response_share=0.43)["sub_scores"][
            "communication_patterns"]
        assert info["value"] == "43% of community issues answered within a week"

    def test_no_issues_to_assess(self, collector):
        info = self._score(collector)["sub_scores"]["communication_patterns"]
        assert not info["passing"]
        assert info["value"] == "No issues to assess"

    def test_participation_combines_issues_and_prs(self, collector):
        # 10 of 60 = 17%, over the 15% bar.
        result = self._score(collector, outside_authors=10)
        info = result["sub_scores"]["community_participation"]
        assert info["passing"]
        assert "60 issues and PRs" in info["value"]

    def test_participation_threshold(self, collector):
        assert not self._score(collector, outside_authors=8)["sub_scores"][
            "community_participation"]["passing"]   # 13%
        assert self._score(collector, outside_authors=9)["sub_scores"][
            "community_participation"]["passing"]   # 15%


class TestIssueStats:
    """Regression cover for the sampling and consistency computations."""

    def _issues(self, n, comments=0, assoc="MEMBER"):
        is_outsider = assoc not in {"OWNER", "MEMBER", "COLLABORATOR"}
        return [{"comments": comments, "is_outsider": is_outsider,
                 "created_at": None, "closed_at": None} for _ in range(n)]

    def test_maintainer_associations_are_not_outside(self, collector):
        for assoc in ["OWNER", "MEMBER", "COLLABORATOR"]:
            stats = collector._compute_issue_stats(self._issues(3, assoc=assoc), [None] * 3)
            assert stats["outside_authors"] == 0

    def test_outside_associations_counted(self, collector):
        for assoc in ["CONTRIBUTOR", "NONE", None]:
            stats = collector._compute_issue_stats(self._issues(3, assoc=assoc), [None] * 3)
            assert stats["outside_authors"] == 3

    def test_unanswered_issues_count_against_timeliness(self, collector):
        # Two answered quickly, two never answered -> 50%, not 100%.
        stats = collector._compute_issue_stats(self._issues(4, assoc="NONE"), [1.0, 2.0, None, None])
        assert stats["timely_response_share"] == 0.5

    def test_slow_responses_are_not_timely(self, collector):
        stats = collector._compute_issue_stats(self._issues(2, assoc="NONE"), [1.0, 500.0])
        assert stats["timely_response_share"] == 0.5

    def test_median_comments(self, collector):
        issues = [{"comments": c, "is_outsider": True,
                   "created_at": None, "closed_at": None} for c in [0, 4, 6]]
        assert collector._compute_issue_stats(issues, [None] * 3)["median_comments"] == 4

    def test_empty_sample(self, collector):
        stats = collector._compute_issue_stats([], [])
        assert stats["timely_response_share"] is None
        assert stats["median_comments"] is None


class TestInternalTriageExclusion:
    """Maintainer-filed, zero-comment issues are self-contained triage
    records, not a conversation -- counting them as unanswered community
    questions misreads a deliberate, effective triage workflow as
    disengagement. corsa-center/metrics#48.
    """

    def _issue(self, assoc, comments):
        return {"comments": comments, "author_association": assoc,
                "is_outsider": assoc not in ("OWNER", "MEMBER", "COLLABORATOR"),
                "created_at": None, "closed_at": None}

    def test_maintainer_zero_comment_issues_excluded_from_median(self, collector):
        # AMReX's shape: a batch of maintainer-filed audit defects, each
        # closed by its own fixing PR, carrying no discussion.
        issues = [self._issue("MEMBER", 0) for _ in range(5)] + [
            self._issue("CONTRIBUTOR", 3),
        ]
        stats = collector._compute_issue_stats(issues, [None] * 6)
        # Without the exclusion this would be median([0,0,0,0,0,3]) == 0.
        assert stats["median_comments"] == 3
        assert stats["internal_triage_excluded"] == 5
        assert stats["discussion_sample_size"] == 1

    def test_maintainer_issue_with_any_comment_still_counts(self, collector):
        # A real conversation, even a short one, is not silent triage.
        issues = [self._issue("MEMBER", 1)]
        stats = collector._compute_issue_stats(issues, [None])
        assert stats["internal_triage_excluded"] == 0
        assert stats["discussion_sample_size"] == 1

    def test_outside_author_zero_comment_issue_still_counts(self, collector):
        # The exclusion is specifically about maintainer-filed self-triage,
        # not "any quiet issue" -- an unanswered external question is a
        # real signal, not a bookkeeping ticket.
        issues = [self._issue("CONTRIBUTOR", 0)]
        stats = collector._compute_issue_stats(issues, [None])
        assert stats["internal_triage_excluded"] == 0
        assert stats["outside_authors"] == 1

    def test_internal_triage_excluded_from_timeliness(self, collector):
        # AMReX's sample: 25 silent maintainer tickets, and every outside
        # issue answered within hours. Was 3/28 = 11%; now 3/3.
        issues = [self._issue("MEMBER", 0) for _ in range(25)] + [
            self._issue("NONE", 2) for _ in range(3)
        ]
        stats = collector._compute_issue_stats(issues, [None] * 25 + [4.0, 10.0, 30.0])
        assert stats["timely_response_share"] == 1.0

    def test_unanswered_community_issue_still_counts_against_timeliness(self, collector):
        issues = [self._issue("MEMBER", 0), self._issue("NONE", 0), self._issue("NONE", 1)]
        stats = collector._compute_issue_stats(issues, [None, None, 5.0])
        assert stats["timely_response_share"] == 0.5

    def test_all_internal_triage_reports_no_median(self, collector):
        issues = [self._issue("OWNER", 0) for _ in range(3)]
        stats = collector._compute_issue_stats(issues, [None] * 3)
        assert stats["median_comments"] is None
        assert stats["discussion_sample_size"] == 0
        assert stats["sample_size"] == 3

    def test_sample_size_unaffected_by_exclusion(self, collector):
        # sample_size stays the true fetched count; discussion_sample_size
        # is the separate, trimmed denominator used for participation.
        issues = [self._issue("MEMBER", 0) for _ in range(4)]
        stats = collector._compute_issue_stats(issues, [None] * 4)
        assert stats["sample_size"] == 4
        assert stats["discussion_sample_size"] == 0


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


class TestThinDiscussionSample:
    """Discussion rows judged on one or two issues are noise, not evidence."""

    def _score(self, collector, n, **issue_overrides):
        issue_stats = {"median_first_response_hours": 1, "median_close_time_hours": 1,
                       "sample_size": 30, "discussion_sample_size": n,
                       "median_comments": 1, "timely_response_share": 0.0,
                       "outside_authors": 0, **issue_overrides}
        pr_stats = {"sample_size": 30, "merge_rate_pct": 90, "outside_authors": 0}
        flow = {"cohort_size": 10, "median_close_hours": 1, "opened": 5, "closed": 10}
        return collector._score(issue_stats, pr_stats, {}, flow)

    def test_thin_sample_rows_are_reported_but_unscored(self, collector):
        result = self._score(collector, 3)
        for key in ("engagement_quality", "communication_patterns"):
            row = result["sub_scores"][key]
            assert row["insufficient_sample"] is True
            assert "only 3 community issue(s)" in row["value"]
        assert result["max_score"] == 5

    def test_adequate_sample_is_scored(self, collector):
        result = self._score(collector, 5)
        assert "insufficient_sample" not in result["sub_scores"]["engagement_quality"]
        assert result["max_score"] == 7


class TestFetchIssuesSamplesDiscussion:
    """Paging continues past internal triage tickets until the discussion
    sample is full, rather than stopping at 30 raw issues."""

    def test_pages_until_enough_discussion_issues(self, collector):
        triage = [{"number": n, "comments": 0, "author_association": "MEMBER", "is_outsider": False} for n in range(80)]
        community = [{"number": 100 + n, "comments": 1, "author_association": "NONE", "is_outsider": True} for n in range(60)]
        pages = [triage + community[:20], community[20:], []]

        collector.forge.issue_list = triage + community
        issues = asyncio.run(collector._fetch_issues(None, "o/r"))
        discussable = [i for i in issues if i["author_association"] == "NONE"]
        assert len(discussable) == 30
        assert collector.forge.calls.count(("issues",)) == 2


class TestIssueFlowScoring:
    def _sub(self, collector, **flow):
        return collector._score({}, {}, {}, flow)["sub_scores"]

    def test_cohort_median_under_threshold_passes(self, collector):
        row = self._sub(collector, cohort_size=100, cohort_still_open=18, median_close_hours=469)["issue_resolution"]
        assert row["passing"] is True
        assert row["value"].startswith("469 hours median to close, for 100 issues opened 30-365 days ago")

    def test_more_than_half_still_open_fails(self, collector):
        row = self._sub(collector, cohort_size=10, cohort_still_open=6,
                        median_close_hours=float("inf"))["issue_resolution"]
        assert row["passing"] is False
        assert "over half still open" in row["value"]

    def test_closing_more_than_opening_passes(self, collector):
        row = self._sub(collector, opened=162, closed=197)["support_closure"]
        assert row["passing"] is True
        assert row["value"] == "162 opened, 197 closed 30-365 days ago (0.82 opened per closed)"

    def test_opening_twice_as_many_as_closing_fails(self, collector):
        assert self._sub(collector, opened=200, closed=100)["support_closure"]["passing"] is False

    def test_nothing_closed_fails(self, collector):
        assert self._sub(collector, opened=5, closed=0)["support_closure"]["passing"] is False

    def test_unmeasured_rows_are_excluded_not_failed(self, collector):
        result = collector._score(_SAMPLED, {}, {}, {})
        assert result["sub_scores"]["issue_resolution"]["not_collected"] is True
        assert result["sub_scores"]["support_closure"]["not_collected"] is True
        assert result["max_score"] == 5

    def test_no_issues_at_all_is_not_measurable(self, collector):
        assert self._sub(collector, opened=0, closed=0)["support_closure"]["not_collected"] is True


class TestIssueFlowQueries:
    def test_cohort_counts_open_issues_as_unresolved(self, collector):
        items = [{"created_at": "2026-01-01T00:00:00Z", "closed_at": "2026-01-02T00:00:00Z"},
                 {"created_at": "2026-01-01T00:00:00Z", "closed_at": None},
                 {"created_at": "2026-01-01T00:00:00Z", "closed_at": "2026-01-01T12:00:00Z"}]
        collector.forge.opened_between = {"items": items, "total_count": 40}
        collector.forge.closed_between = 50
        flow = asyncio.run(collector._issue_flow(None, "o/r"))
        assert flow["cohort_size"] == 3 and flow["cohort_still_open"] == 1
        assert flow["median_close_hours"] == 24
        assert (flow["opened"], flow["closed"]) == (40, 50)

    def test_search_failure_leaves_rows_unmeasured(self, collector):
        collector.forge.opened_between = None
        collector.forge.closed_between = None
        flow = asyncio.run(collector._issue_flow(None, "o/r"))
        assert "cohort_size" not in flow and "opened" not in flow

    def test_closed_count_unavailable_leaves_closure_unmeasured(self, collector):
        # GitLab can't count issues closed in a window; the cohort still
        # measures resolution, but the opened/closed comparison is skipped.
        collector.forge.opened_between = {"items": [], "total_count": 5}
        collector.forge.closed_between = None
        flow = asyncio.run(collector._issue_flow(None, "o/r"))
        assert flow["cohort_size"] == 0
        assert "opened" not in flow and "closed" not in flow


class TestThinWindows:
    def test_single_issue_window_is_reported_but_unscored(self, collector):
        result = collector._score(_SAMPLED, {}, {}, {"cohort_size": 1, "cohort_still_open": 1,
                                               "median_close_hours": float("inf"),
                                               "opened": 1, "closed": 0})
        for key in ("issue_resolution", "support_closure"):
            row = result["sub_scores"][key]
            assert row["insufficient_sample"] is True
            assert "too few to judge" in row["value"]
        assert result["max_score"] == 5

    def test_response_time_with_no_replies_says_so(self, collector):
        row = collector._score({"median_first_response_hours": None, "sample_size": 30}, {}, {})[
            "sub_scores"]["response_time_tracking"]
        assert row["value"] == "no response to any of 30 sampled issue(s)"
        assert row["passing"] is False

    def test_response_time_on_one_issue_is_unscored(self, collector):
        row = collector._score({"median_first_response_hours": None, "sample_size": 1}, {}, {})[
            "sub_scores"]["response_time_tracking"]
        assert row["insufficient_sample"] is True


class TestNoIssues:
    def test_issue_rows_with_nothing_to_measure_are_unmarked(self, collector):
        result = collector._score({"sample_size": 0, "discussion_sample_size": 0}, {}, {}, {})
        for key in ("response_time_tracking", "engagement_quality", "communication_patterns"):
            row = result["sub_scores"][key]
            assert row["insufficient_sample"] is True, key
            assert "to assess" in row["value"]
