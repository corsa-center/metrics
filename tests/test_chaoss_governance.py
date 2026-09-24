"""Unit tests for CHAOSSGovernanceCollector pure computation methods."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from collectors.ecosystem.base import COLLECTION_GAP, RepoTree
from collectors.ecosystem.chaoss_governance import CHAOSSGovernanceCollector


@pytest.fixture
def collector():
    return CHAOSSGovernanceCollector()


# ------------------------------------------------------------------ #
# _get_documentation_usability                                         #
# ------------------------------------------------------------------ #

class TestDocumentationUsabilityTreeResolution:
    """Contributing guide and docs folder are resolved against a RepoTree
    (case-insensitive) -- see METRIC_BLIND_SPOTS.md class F1.
    """

    def _run(self, collector, tree, readme=None, has_wiki=False):
        async def go():
            with patch.object(collector, "_get_readme_content", new=AsyncMock(return_value=readme)), \
                 patch.object(collector, "_check_wiki_enabled", new=AsyncMock(return_value=has_wiki)):
                return await collector._get_documentation_usability(None, "o", "r", tree)

        return asyncio.run(go())

    def test_gapped_tree_marks_whole_result_not_collected(self, collector):
        result = self._run(collector, COLLECTION_GAP)
        assert result["not_collected"] is True

    def test_differently_cased_contributing_guide_found(self, collector):
        # ADIOS2 names its guide Contributing.md.
        tree = RepoTree("o", "r", ["Contributing.md"], truncated=False)
        result = self._run(collector, tree)
        assert "contributing" in result["found"]
        assert result["details"]["contributing"]["exists"] is True

    def test_capitalized_docs_directory_found(self, collector):
        # AMReX-Codes/amrex ships "Docs", superlu ships "DOC".
        tree = RepoTree("o", "r", ["Docs/index.rst"], truncated=False)
        result = self._run(collector, tree)
        assert "docs_folder" in result["found"]
        assert result["details"]["docs_folder"]["path"] == "docs"

    def test_confirmed_absence_is_not_a_gap(self, collector):
        tree = RepoTree("o", "r", ["README.md"], truncated=False)
        result = self._run(collector, tree)
        assert "not_collected" not in result
        assert result["details"]["contributing"]["exists"] is False
        assert result["details"]["docs_folder"]["exists"] is False


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


class TestNotCollectedExclusion:
    """This is the fix for the 2026-09-16 incident: kokkos/kokkos's CHAOSS
    section reached the dashboard as a confident "0.0/100 (critical)" when
    every category had actually gapped (rate-limited, not genuinely zero).
    A not_collected category must be excluded from the weighted average --
    not counted as a 0 -- with the remaining weights re-normalized.
    """

    def test_one_gapped_category_is_excluded_and_weights_renormalize(self, collector):
        # Every real category scores 100 except popularity, which gapped.
        # If popularity silently counted as 0, the result would be
        # 100 * (1 - 0.15) = 85, not 100.
        result = collector._calculate_overall_score(
            {"not_collected": True},
            {"score": 100},
            {"time_to_close": {"score": 100}, "issue_age": {"score": 100}},
            {"closure_ratio": {"score": 100}},
            {"score": 100},
            {"score": 100},
        )
        assert result["score"] == 100.0
        assert result["category_scores"]["project_popularity"] == {"not_collected": True}
        assert result["coverage"] == pytest.approx(0.85)

    def test_everything_gapped_reports_no_score_not_zero(self, collector):
        gap = {"not_collected": True}
        result = collector._calculate_overall_score(
            gap, gap, {"time_to_close": gap, "issue_age": gap}, {"closure_ratio": gap}, gap, gap
        )
        assert result["score"] is None
        assert result["status"] == "not_collected"
        assert all(v == {"not_collected": True} for v in result["category_scores"].values())

    def test_fully_collected_result_is_unaffected(self, collector):
        # No not_collected anywhere -- behavior must match the pre-existing
        # weighted-average path exactly (see TestCalculateOverallScore).
        result = self._score_all(collector, 70)
        assert result["score"] == 70.0
        assert result["coverage"] == 1.0

    @staticmethod
    def _score_all(collector, value):
        return collector._calculate_overall_score(
            {"score": value},
            {"score": value},
            {"time_to_close": {"score": value}, "issue_age": {"score": value}},
            {"closure_ratio": {"score": value}},
            {"score": value},
            {"score": value},
        )


class TestGapPropagatesThroughAggregation:
    """_calculate_time_to_close/_calculate_issue_age must not treat
    COLLECTION_GAP (couldn't fetch) the same as an empty list (confirmed:
    this repo really has zero closed/open issues) -- both are falsy, but
    only one is a trustworthy result.
    """

    def test_time_to_close_gap_is_not_collected(self, collector):
        assert collector._calculate_time_to_close(COLLECTION_GAP) == {"not_collected": True}

    def test_time_to_close_genuinely_empty_is_a_real_zero(self, collector):
        result = collector._calculate_time_to_close([])
        assert result.get("not_collected") is not True
        assert result["count"] == 0

    def test_issue_age_gap_is_not_collected(self, collector):
        assert collector._calculate_issue_age(COLLECTION_GAP) == {"not_collected": True}

    def test_issue_age_genuinely_empty_is_a_real_zero(self, collector):
        result = collector._calculate_issue_age([])
        assert result.get("not_collected") is not True
        assert result["count"] == 0


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
