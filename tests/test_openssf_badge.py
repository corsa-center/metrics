"""Unit tests for OpenSSFBadgeCollector pure computation methods."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from collectors.ecosystem.base import COLLECTION_GAP
from collectors.ecosystem.openssf_badge import OpenSSFBadgeCollector


@pytest.fixture
def collector():
    return OpenSSFBadgeCollector()


# ------------------------------------------------------------------ #
# _get_badge_level                                                     #
# ------------------------------------------------------------------ #

class TestGetBadgeLevel:
    # bestpractices.dev returns badge_level as a human-readable string
    # directly ("passing"/"silver"/"gold"/"in_progress") — verified live
    # against real projects, not a numeric code needing translation.
    def test_gold(self, collector):
        assert collector._get_badge_level({"badge_level": "gold"}) == "gold"

    def test_silver(self, collector):
        assert collector._get_badge_level({"badge_level": "silver"}) == "silver"

    def test_passing(self, collector):
        assert collector._get_badge_level({"badge_level": "passing"}) == "passing"

    def test_in_progress_from_api_field(self, collector):
        assert collector._get_badge_level({"badge_level": "in_progress", "badge_percentage_0": 85}) == "in_progress"

    def test_in_progress_when_percentage_nonzero_and_level_missing(self, collector):
        assert collector._get_badge_level({"badge_percentage_0": 45}) == "in_progress"

    def test_none_when_no_data(self, collector):
        assert collector._get_badge_level({}) == "none"

    def test_none_when_zero_percentage(self, collector):
        assert collector._get_badge_level({"badge_percentage_0": 0}) == "none"


# ------------------------------------------------------------------ #
# _assess_criteria_from_badge                                          #
# ------------------------------------------------------------------ #

class TestAssessCriteriaFromBadge:
    def test_all_met(self, collector):
        badge = {"governance_status": "Met", "contribution_status": "Met"}
        result = collector._assess_criteria_from_badge(badge, ["governance", "contribution"])
        assert result["count_found"] == 2
        assert result["percentage"] == 100.0
        assert result["missing"] == []

    def test_none_met(self, collector):
        badge = {"governance_status": "Unmet", "contribution_status": "Unmet"}
        result = collector._assess_criteria_from_badge(badge, ["governance", "contribution"])
        assert result["count_found"] == 0
        assert result["percentage"] == 0.0
        assert set(result["missing"]) == {"governance", "contribution"}

    def test_partial(self, collector):
        badge = {"governance_status": "Met", "contribution_status": "Unknown"}
        result = collector._assess_criteria_from_badge(badge, ["governance", "contribution"])
        assert result["count_found"] == 1
        assert result["percentage"] == 50.0

    def test_empty_criteria(self, collector):
        result = collector._assess_criteria_from_badge({}, [])
        assert result["percentage"] == 0
        assert result["count_total"] == 0

    def test_detail_structure(self, collector):
        badge = {"governance_status": "Met"}
        result = collector._assess_criteria_from_badge(badge, ["governance"])
        assert result["details"]["governance"] == {"status": "Met", "met": True}


# ------------------------------------------------------------------ #
# _assess_governance/security/quality_from_badge (delegation checks) #
# ------------------------------------------------------------------ #

class TestAssessDelegation:
    def test_governance_criteria_keys(self, collector):
        result = collector._assess_governance_from_badge({})
        assert "governance" in result["missing"]
        assert "code_of_conduct" in result["missing"]

    def test_security_criteria_keys(self, collector):
        result = collector._assess_security_from_badge({})
        assert "security_policy" in result["missing"]

    def test_quality_criteria_keys(self, collector):
        result = collector._assess_quality_from_badge({})
        assert "test" in result["missing"]
        assert "documentation_basics" in result["missing"]


# ------------------------------------------------------------------ #
# _collect_with_badge (pure logic, no I/O)                            #
# ------------------------------------------------------------------ #

class TestCollectWithBadge:
    def test_passing_badge(self, collector):
        badge_data = {
            "badge_level": "passing",
            "badge_percentage_0": 100,
            "id": 42,
        }
        result = collector._collect_with_badge("MyPkg", "owner", "repo", badge_data)
        assert result["badge_exists"] is True
        assert result["badge_status"]["level"] == "passing"
        assert result["badge_status"]["progress_percentage"] == 100
        assert result["overall_score"]["status"] == "passing"
        assert result["assessment_method"] == "openssf_badge_api"

    def test_in_progress_badge(self, collector):
        badge_data = {"badge_level": None, "badge_percentage_0": 65, "id": 7}
        result = collector._collect_with_badge("Pkg", "o", "r", badge_data)
        assert result["badge_status"]["in_progress"] is True
        assert result["overall_score"]["status"] == "in_progress"


class TestScanFilesGapHandling:
    """The no-badge path is already an admitted proxy ("estimated": True),
    but a gap is a different kind of uncertainty than that estimate and
    must not be silently folded into "missing".
    """

    def _run(self, collector, file_map, responses):
        """responses: dict[pattern] -> return value for _check_file_exists."""
        async def fake_check(client, owner, repo, pattern):
            return responses.get(pattern, None)

        async def go():
            with patch.object(collector, "_check_file_exists", side_effect=fake_check):
                return await collector._scan_files(None, "o", "r", file_map)

        return asyncio.run(go())

    def test_gapped_criterion_is_not_collected_not_missing(self, collector):
        file_map = {"code_of_conduct": ["CODE_OF_CONDUCT.md"]}
        result = self._run(collector, file_map, {"CODE_OF_CONDUCT.md": COLLECTION_GAP})
        assert result["missing"] == []
        assert result["not_collected"] == ["code_of_conduct"]

    def test_confirmed_absent_is_still_a_real_miss(self, collector):
        file_map = {"code_of_conduct": ["CODE_OF_CONDUCT.md"]}
        result = self._run(collector, file_map, {"CODE_OF_CONDUCT.md": None})
        assert result["missing"] == ["code_of_conduct"]
        assert result["not_collected"] == []

    def test_all_gapped_reports_no_percentage(self, collector):
        file_map = {"a": ["A.md"], "b": ["B.md"]}
        result = self._run(collector, file_map, {"A.md": COLLECTION_GAP, "B.md": COLLECTION_GAP})
        assert result["percentage"] is None
        assert result["count_total"] == 0

    def test_mixed_gap_and_confirmed_renormalizes_percentage(self, collector):
        file_map = {"found_one": ["F.md"], "gapped": ["G.md"]}
        result = self._run(collector, file_map, {"F.md": "http://x", "G.md": COLLECTION_GAP})
        # gapped criterion excluded from the denominator entirely
        assert result["count_total"] == 1
        assert result["percentage"] == 100.0


class TestCollectWithoutBadgeGapHandling:
    def test_one_category_fully_gapped_renormalizes_overall(self, collector):
        async def go():
            async def fake_scan(client, owner, repo, file_map):
                if file_map is collector.GOVERNANCE_FILES:
                    return {"percentage": None}  # totally gapped
                return {"percentage": 100.0}

            with patch.object(collector, "_scan_files", side_effect=fake_scan):
                return await collector._collect_without_badge(None, "Pkg", "o", "r")

        result = asyncio.run(go())
        # If the gap silently counted as 0%, this would be 60% (0.3+0.3 of 100).
        assert result["overall_score"]["percentage"] == 100.0

    def test_everything_gapped_reports_not_collected_status(self, collector):
        async def go():
            async def fake_scan(client, owner, repo, file_map):
                return {"percentage": None}

            with patch.object(collector, "_scan_files", side_effect=fake_scan):
                return await collector._collect_without_badge(None, "Pkg", "o", "r")

        result = asyncio.run(go())
        assert result["overall_score"]["score"] is None
        assert result["overall_score"]["status"] == "not_collected"
