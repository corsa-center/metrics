"""Unit tests for CHAOSSGovernanceCollector pure computation methods."""

import pytest
from collectors.sustainability.chaoss_governance import CHAOSSGovernanceCollector


@pytest.fixture
def collector():
    return CHAOSSGovernanceCollector()


# ------------------------------------------------------------------ #
# _assess_readme_quality                                               #
# ------------------------------------------------------------------ #

class TestAssessReadmeQuality:
    def test_empty_returns_zero(self, collector):
        assert collector._assess_readme_quality("") == 0.0

    def test_all_sections_present(self, collector):
        content = (
            "## Installation\nsteps\n"
            "## Usage\nexamples\n"
            "## Contributing\ninfo\n"
            "## License\nMIT\n"
            "## About\ndescription\n"
        )
        assert collector._assess_readme_quality(content) == 100.0

    def test_no_sections(self, collector):
        assert collector._assess_readme_quality("just a short file") == 0.0

    def test_length_bonus(self, collector):
        # Only a license section (20 pts) + length bonus (+10)
        content = "## License MIT\n" + "x" * 1001
        score = collector._assess_readme_quality(content)
        assert score == 30.0

    def test_badge_bonus(self, collector):
        # Use a URL that doesn't trigger any section regex
        content = "## License MIT\n![ci](https://ci.server/badge.svg)"
        score = collector._assess_readme_quality(content)
        assert score == 30.0  # 20 (license) + 10 (badge)

    def test_capped_at_100(self, collector):
        content = (
            "## Installation\n## Usage\n## Contributing\n## License\n## About\n"
            "![badge](url)\n" + "x" * 1001
        )
        assert collector._assess_readme_quality(content) == 100.0


# ------------------------------------------------------------------ #
# _calculate_time_to_close                                             #
# ------------------------------------------------------------------ #

class TestCalculateTimeToClose:
    def test_empty_list(self, collector):
        result = collector._calculate_time_to_close([])
        assert result["count"] == 0
        assert result["score"] == 0

    def test_fast_resolution(self, collector):
        issues = [
            {"created_at": "2024-01-01T00:00:00Z", "closed_at": "2024-01-03T00:00:00Z"},
        ]
        result = collector._calculate_time_to_close(issues)
        assert result["avg_days"] == 2.0
        assert result["score"] == 100  # <= 7 days

    def test_medium_resolution(self, collector):
        issues = [
            {"created_at": "2024-01-01T00:00:00Z", "closed_at": "2024-01-20T00:00:00Z"},
        ]
        result = collector._calculate_time_to_close(issues)
        assert result["score"] == 80  # <= 30 days

    def test_slow_resolution(self, collector):
        issues = [
            {"created_at": "2024-01-01T00:00:00Z", "closed_at": "2024-06-01T00:00:00Z"},
        ]
        result = collector._calculate_time_to_close(issues)
        assert result["score"] == 40  # <= 180 days

    def test_very_slow_resolution(self, collector):
        issues = [
            {"created_at": "2023-01-01T00:00:00Z", "closed_at": "2024-01-01T00:00:00Z"},
        ]
        result = collector._calculate_time_to_close(issues)
        assert result["score"] == 20  # > 180 days

    def test_missing_dates_skipped(self, collector):
        issues = [
            {"created_at": None, "closed_at": "2024-01-03T00:00:00Z"},
            {"created_at": "2024-01-01T00:00:00Z", "closed_at": "2024-01-04T00:00:00Z"},
        ]
        result = collector._calculate_time_to_close(issues)
        assert result["count"] == 1
        assert result["avg_days"] == 3.0


# ------------------------------------------------------------------ #
# _calculate_overall_score                                             #
# ------------------------------------------------------------------ #

class TestCalculateOverallScore:
    def _make_score(self, collector, pop=0, doc=0, ttc=0, age=0, pr=0, rel=0, incl=0):
        popularity = {"score": pop}
        documentation = {"score": doc}
        issue_metrics = {"time_to_close": {"score": ttc}, "issue_age": {"score": age}}
        pr_metrics = {"closure_ratio": {"score": pr}}
        release_freq = {"score": rel}
        inclusivity = {"score": incl}
        return collector._calculate_overall_score(
            popularity, documentation, issue_metrics, pr_metrics, release_freq, inclusivity
        )

    def test_all_zeros_is_critical(self, collector):
        result = self._make_score(collector)
        assert result["score"] == 0.0
        assert result["status"] == "critical"

    def test_all_perfect_is_excellent(self, collector):
        result = self._make_score(collector, 100, 100, 100, 100, 100, 100, 100)
        assert result["score"] == 100.0
        assert result["status"] == "excellent"

    def test_status_good(self, collector):
        result = self._make_score(collector, 70, 70, 70, 70, 70, 70, 70)
        assert result["status"] == "good"

    def test_status_fair(self, collector):
        result = self._make_score(collector, 40, 40, 40, 50, 50, 50, 50)
        assert result["status"] == "fair"

    def test_weights_sum_to_one(self, collector):
        # All inputs = 1 → weighted score should equal 1 × sum-of-weights = 1.0
        result = self._make_score(collector, 1, 1, 1, 1, 1, 1, 1)
        assert abs(result["score"] - 1.0) < 1e-9

    def test_category_scores_present(self, collector):
        result = self._make_score(collector, pop=50)
        cats = result["category_scores"]
        assert cats["project_popularity"] == 50
        assert cats["documentation_usability"] == 0


# ------------------------------------------------------------------ #
# _parse_date                                                          #
# ------------------------------------------------------------------ #

class TestParseDate:
    def test_none_returns_none(self, collector):
        assert collector._parse_date(None) is None

    def test_z_suffix(self, collector):
        dt = collector._parse_date("2024-01-15T12:00:00Z")
        assert dt is not None
        assert dt.year == 2024
        assert dt.tzinfo is not None

    def test_offset_format(self, collector):
        dt = collector._parse_date("2024-06-01T00:00:00+00:00")
        assert dt is not None
        assert dt.month == 6

    def test_invalid_returns_none(self, collector):
        assert collector._parse_date("not-a-date") is None
