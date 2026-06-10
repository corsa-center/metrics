"""Unit tests for CICDMetricsCollector pure computation methods."""

import pytest
from collectors.quality.development_practices.ci_cd import CICDMetricsCollector


CONFIG = {
    "api_credentials": {
        "github": {"token": ""},
    }
}


@pytest.fixture
def collector():
    return CICDMetricsCollector(CONFIG)


# ------------------------------------------------------------------ #
# _parse_repo_url                                                      #
# ------------------------------------------------------------------ #

class TestParseRepoUrl:
    def test_https_url(self, collector):
        assert collector._parse_repo_url("https://github.com/owner/repo") == \
            "https://api.github.com/repos/owner/repo"

    def test_git_suffix_stripped(self, collector):
        assert collector._parse_repo_url("https://github.com/owner/repo.git") == \
            "https://api.github.com/repos/owner/repo"

    def test_trailing_slash_stripped(self, collector):
        assert collector._parse_repo_url("https://github.com/owner/repo/") == \
            "https://api.github.com/repos/owner/repo"

    def test_invalid_url_raises(self, collector):
        with pytest.raises(ValueError):
            collector._parse_repo_url("https://gitlab.com/owner/repo")


# ------------------------------------------------------------------ #
# _calculate_score                                                     #
# ------------------------------------------------------------------ #

class TestCalculateScore:
    def _base_results(self):
        return {
            "average_workflow_execution_time": 0,
            "workflow_success_percentage": {},
            "total_workflow_success_percentage": 0,
            "num_of_deployments_last_365_days": 0,
            "num_of_releases_last_365_days": 0,
            "average_time_to_failure": 0,
            "average_cycle_time": 0,
        }

    def test_all_zeros_score_zero(self, collector):
        result = collector._calculate_score(self._base_results())
        assert result["score"] == 0

    def test_fast_workflow_scores(self, collector):
        r = self._base_results()
        r["average_workflow_execution_time"] = 1800  # 30 minutes < 1 hour
        result = collector._calculate_score(r)
        assert result["score"] >= 1

    def test_high_success_rate_scores(self, collector):
        r = self._base_results()
        r["total_workflow_success_percentage"] = 80
        result = collector._calculate_score(r)
        assert result["score"] >= 1

    def test_deployments_present_scores(self, collector):
        r = self._base_results()
        r["num_of_deployments_last_365_days"] = 5
        result = collector._calculate_score(r)
        assert result["score"] >= 1

    def test_releases_present_scores(self, collector):
        r = self._base_results()
        r["num_of_releases_last_365_days"] = 2
        result = collector._calculate_score(r)
        assert result["score"] >= 1

    def test_fast_cycle_time_scores(self, collector):
        r = self._base_results()
        r["average_cycle_time"] = 3600  # 1 hour < 168 hours
        result = collector._calculate_score(r)
        assert result["score"] >= 1

    def test_slow_cycle_time_does_not_score(self, collector):
        r = self._base_results()
        r["average_cycle_time"] = 7 * 24 * 3600 + 1  # just over 1 week
        result = collector._calculate_score(r)
        # Only cycle_time check, and it fails, so score stays 0
        assert result["score"] == 0

    def test_none_deployments_reduces_max_score(self, collector):
        r = self._base_results()
        r["num_of_deployments_last_365_days"] = None
        r["num_of_releases_last_365_days"] = None
        result = collector._calculate_score(r)
        assert result["max_score"] == 4  # 6 - 2

    def test_percentage_computed(self, collector):
        r = self._base_results()
        r["total_workflow_success_percentage"] = 70
        result = collector._calculate_score(r)
        assert result["percentage"] == round(result["score"] / result["max_score"] * 100, 2)

    def test_average_time_to_failure_uses_correct_key(self, collector):
        # Regression: original code used average_workflow_execution_time here by mistake.
        r = self._base_results()
        r["average_time_to_failure"] = 1800  # 30 min < 1 hour  → should score
        r["average_workflow_execution_time"] = 99999  # slow, should NOT score
        result = collector._calculate_score(r)
        # Score should count time_to_failure (1pt) but not exec_time (0pt)
        assert result["score"] >= 1

    def test_all_elite_scores_max(self, collector):
        r = {
            "average_workflow_execution_time": 1800,   # < 1h
            "total_workflow_success_percentage": 95,    # > 60%
            "num_of_deployments_last_365_days": 10,    # >= 1
            "num_of_releases_last_365_days": 3,        # >= 1
            "average_time_to_failure": 600,            # < 1h
            "average_cycle_time": 3600,                # < 1 week
        }
        result = collector._calculate_score(r)
        assert result["score"] == result["max_score"]
        assert result["percentage"] == 100.0
