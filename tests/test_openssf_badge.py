"""Unit tests for OpenSSFBadgeCollector pure computation methods."""

import pytest
from collectors.sustainability.openssf_badge import OpenSSFBadgeCollector


@pytest.fixture
def collector():
    return OpenSSFBadgeCollector()


# ------------------------------------------------------------------ #
# _get_badge_level                                                     #
# ------------------------------------------------------------------ #

class TestGetBadgeLevel:
    def test_gold(self, collector):
        assert collector._get_badge_level({"badge_level": "2"}) == "gold"

    def test_silver(self, collector):
        assert collector._get_badge_level({"badge_level": "1"}) == "silver"

    def test_passing(self, collector):
        assert collector._get_badge_level({"badge_level": "0"}) == "passing"

    def test_in_progress_when_percentage_nonzero(self, collector):
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
            "badge_level": "0",
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
