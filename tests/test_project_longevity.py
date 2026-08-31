"""Unit tests for the 4.2.10 Project Longevity and Community Health section.

4.2.10 has no collector of its own — the orchestrator re-derives all five
sub-metrics from data ActiveMaintenanceCollector already gathered for 4.2.3.
These tests exercise that transform directly.
"""

import pytest
from orchestrator import MetricsOrchestrator


def _maint(**overrides):
    """A healthy, mature project; override individual blocks per test."""
    base = {
        "maintenance_indicators": {
            "archived": False,
            "maintenance_signals": [],
            "days_since_last_push": 3,
            "created_at": "2020-04-24T18:25:20Z",
            "first_commit_date": "1997-07-30T21:17:56Z",
            "repo_age_years": 6.3,
            "history_age_years": 29.1,
            "project_age_years": 29.1,
        },
        "commit_activity": {
            "total_commits_52w": 1200,
            "active_weeks_52w": 48,
            "recent_trend": "stable",
        },
        "release_activity": {"releases_last_year": 3},
        "contributor_activity": {
            "total_contributors": 90,
            "bus_factor": 5,
            "top_contributor_pct": 22,
        },
    }
    base.update(overrides)
    return base


def _render(maintenance):
    orch = MetricsOrchestrator.__new__(MetricsOrchestrator)
    orch.config = {}
    metrics = {
        "dimensions": {"sustainability": {"sub_results": {"maintenance": maintenance}}}
    }
    out = orch._transform_for_dashboard("HDFGroup/hdf5", metrics)
    return out["sustainability"]["4.2.10"]["data"]


class TestSectionShape:
    def test_all_five_submetrics_present(self):
        html = _render(_maint())
        for label in [
            "Comprehensive Activity Analysis",
            "Contributor Viability Assessment",
            "Maintenance Mode Detection",
            "Community Health Trends",
            "Project Lifecycle Assessment",
        ]:
            assert f"<strong>{label}:</strong>" in html

    def test_no_maintenance_data_returns_none(self):
        assert _render({}) is None


class TestScoring:
    def test_healthy_project_scores_full(self):
        html = _render(_maint())
        assert "<strong>Score:</strong> 5/5" in html

    def test_archived_project_scores_zero(self):
        html = _render(
            _maint(
                maintenance_indicators={
                    "archived": True,
                    "maintenance_signals": ["deprecated"],
                    "days_since_last_push": 900,
                    "created_at": "2018-01-01T00:00:00Z",
                    "first_commit_date": "2018-01-02T00:00:00Z",
                    "repo_age_years": 8.7,
                    "history_age_years": 8.7,
                    "project_age_years": 8.7,
                },
                commit_activity={
                    "total_commits_52w": 0,
                    "active_weeks_52w": 0,
                    "recent_trend": "inactive",
                },
                release_activity={"releases_last_year": 0},
                contributor_activity={
                    "total_contributors": 2,
                    "bus_factor": 1,
                    "top_contributor_pct": 95,
                },
            )
        )
        assert "<strong>Score:</strong> 0/5" in html
        assert "Retired" in html


class TestActivityAnalysis:
    def test_two_of_three_dimensions_passes(self):
        # Commits and sustained activity, but no release in the last year.
        html = _render(_maint(release_activity={"releases_last_year": 0}))
        assert "2/3 dimensions active ✓" in html

    def test_one_of_three_dimensions_fails(self):
        html = _render(
            _maint(
                commit_activity={
                    "total_commits_52w": 5,
                    "active_weeks_52w": 3,
                    "recent_trend": "declining",
                },
                release_activity={"releases_last_year": 0},
            )
        )
        assert "1/3 dimensions active ✗" in html


class TestContributorViability:
    def test_bus_factor_threshold_matches_4_3_6(self):
        # active_maintenance.py and 4.3.6 both treat >= 3 as healthy.
        assert "Bus factor 3 ✓" in _render(
            _maint(
                contributor_activity={
                    "total_contributors": 8,
                    "bus_factor": 3,
                    "top_contributor_pct": 40,
                }
            )
        )
        assert "Bus factor 2 ✗" in _render(
            _maint(
                contributor_activity={
                    "total_contributors": 8,
                    "bus_factor": 2,
                    "top_contributor_pct": 60,
                }
            )
        )


class TestMaintenanceModeDetection:
    def test_stale_push_is_a_warning(self):
        html = _render(
            _maint(
                maintenance_indicators={
                    "archived": False,
                    "maintenance_signals": [],
                    "days_since_last_push": 400,
                    "repo_age_years": 6.0,
                }
            )
        )
        assert "no push in 400 days" in html.lower()

    def test_missing_push_date_is_not_a_warning(self):
        html = _render(
            _maint(
                maintenance_indicators={
                    "archived": False,
                    "maintenance_signals": [],
                    "days_since_last_push": None,
                    "repo_age_years": 6.0,
                }
            )
        )
        assert "Maintenance Mode Detection:</strong> No warning indicators ✓" in html


class TestLifecycleStages:
    @pytest.mark.parametrize(
        "age,releases,commits,expected,passes",
        [
            (1.2, 2, 300, "Emerging", False),
            (3.0, 2, 300, "Growing", True),
            (8.0, 1, 300, "Mature", True),
            (8.0, 0, 0, "Legacy", False),
            (None, 2, 300, "Unknown", False),
        ],
    )
    def test_stage_classification(self, age, releases, commits, expected, passes):
        html = _render(
            _maint(
                maintenance_indicators={
                    "archived": False,
                    "maintenance_signals": [],
                    "days_since_last_push": 3,
                    "project_age_years": age,
                },
                commit_activity={
                    "total_commits_52w": commits,
                    "active_weeks_52w": 40 if commits else 0,
                    "recent_trend": "stable",
                },
                release_activity={"releases_last_year": releases},
            )
        )
        mark = "✓" if passes else "✗"
        assert f"Project Lifecycle Assessment:</strong> {expected} {mark}" in html


class TestOverrides:
    def test_override_replaces_row_and_recounts_score(self):
        html = _render(_maint(commit_activity={
            "total_commits_52w": 1200,
            "active_weeks_52w": 48,
            "recent_trend": "declining",
        }))
        assert "<strong>Score:</strong> 4/5" in html

        overridden = MetricsOrchestrator._apply_section_overrides(
            html, {"Community Health Trends": "N/A"}
        )
        assert "<strong>Community Health Trends:</strong> N/A</p>" in overridden
        # The N/A row no longer counts as a hit, and sub-details stay excluded.
        assert "<strong>Score:</strong> 4/5" in overridden


class TestProjectAgeDates:
    def test_both_dates_are_reported(self):
        html = _render(_maint())
        assert "first commit 1997-07-30 (29.1 yrs)" in html
        assert "GitHub repo created 2020-04-24 (6.3 yrs)" in html

    def test_lifecycle_uses_the_longer_age(self):
        # Repo created 2 years ago but history imported from 29 years back:
        # the project is Mature, not Growing.
        html = _render(_maint())
        assert "Project Lifecycle Assessment:</strong> Mature" in html

    def test_falls_back_to_repo_age_when_history_unknown(self):
        html = _render(
            _maint(
                maintenance_indicators={
                    "archived": False,
                    "maintenance_signals": [],
                    "days_since_last_push": 3,
                    "created_at": "2019-01-01T00:00:00Z",
                    "repo_age_years": 7.7,
                }
            )
        )
        assert "Project Lifecycle Assessment:</strong> Mature" in html
        assert "GitHub repo created 2019-01-01 (7.7 yrs)" in html
        assert "first commit" not in html

    def test_no_dates_reads_as_unknown(self):
        html = _render(
            _maint(
                maintenance_indicators={
                    "archived": False,
                    "maintenance_signals": [],
                    "days_since_last_push": 3,
                }
            )
        )
        assert "Project Lifecycle Assessment:</strong> Unknown ✗" in html
        assert "Project age: age unknown" in html


class TestFirstCommitLookup:
    def test_picks_last_page_from_link_header(self):
        from collectors.sustainability.active_maintenance import ActiveMaintenanceCollector

        header = (
            '<https://api.github.com/repositories/1/commits?per_page=1&page=2>; rel="next", '
            '<https://api.github.com/repositories/1/commits?per_page=1&page=9000>; rel="last"'
        )
        assert ActiveMaintenanceCollector._get_rel_link(header, "last").endswith("page=9000")
        assert ActiveMaintenanceCollector._get_next_link(header).endswith("page=2")

    def test_missing_header_returns_none(self):
        from collectors.sustainability.active_maintenance import ActiveMaintenanceCollector

        assert ActiveMaintenanceCollector._get_rel_link(None, "last") is None
        assert ActiveMaintenanceCollector._get_rel_link("", "last") is None


class TestAgeDerivation:
    def test_longer_of_the_two_ages_wins(self):
        from collectors.sustainability.active_maintenance import ActiveMaintenanceCollector

        c = ActiveMaintenanceCollector()
        out = c._analyze_maintenance_indicators(
            {"archived": False, "description": "", "pushed_at": None,
             "created_at": "2020-04-24T18:25:20Z"},
            first_commit_date="1997-07-30T21:17:56Z",
        )
        assert out["project_age_years"] == out["history_age_years"]
        assert out["project_age_years"] > out["repo_age_years"]

    def test_empty_repo_created_before_first_commit(self):
        # zfp's first commit landed a day after the repo was created; repo age
        # is then the longer span and must not be discarded.
        from collectors.sustainability.active_maintenance import ActiveMaintenanceCollector

        c = ActiveMaintenanceCollector()
        out = c._analyze_maintenance_indicators(
            {"archived": False, "description": "", "pushed_at": None,
             "created_at": "2016-03-24T02:10:02Z"},
            first_commit_date="2016-03-25T22:08:59Z",
        )
        assert out["project_age_years"] == out["repo_age_years"]

    def test_no_dates_yields_none(self):
        from collectors.sustainability.active_maintenance import ActiveMaintenanceCollector

        c = ActiveMaintenanceCollector()
        out = c._analyze_maintenance_indicators(
            {"archived": False, "description": "", "pushed_at": None}
        )
        assert out["project_age_years"] is None
        assert out["repo_age_years"] is None
        assert out["history_age_years"] is None
