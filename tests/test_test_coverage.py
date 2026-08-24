"""Unit tests for TestCoverageCollector."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from collectors.quality.test_coverage import TestCoverageCollector


@pytest.fixture
def collector():
    return TestCoverageCollector()


SAMPLE_ACTIVE_REPO = {
    "active": True,
    "totals": {"coverage": 94.77, "hits": 11897, "lines": 12553},
}

SAMPLE_INACTIVE_REPO = {"active": False, "totals": None}


class TestEmptyResult:
    def test_structure(self, collector):
        result = collector._empty_result("MyPkg")
        assert result["package_name"] == "MyPkg"
        assert result["coverage_exists"] is False
        assert result["coverage_percentage"] is None


class TestNoCoverageResult:
    def test_structure(self, collector):
        result = collector._no_coverage_result("MyPkg", "owner", "repo")
        assert result["coverage_exists"] is False
        assert result["repository"] == "owner/repo"


class TestFetchCoverage:
    def test_successful_fetch(self, collector):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = SAMPLE_ACTIVE_REPO
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        result = asyncio.run(
            collector._fetch_coverage(mock_client, "MyPkg", "owner", "repo")
        )

        assert result["coverage_exists"] is True
        assert result["coverage_percentage"] == 94.8
        assert result["lines_covered"] == 11897
        assert result["lines_total"] == 12553
        assert result["source"] == "codecov"

    def test_inactive_repo_returns_no_coverage(self, collector):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = SAMPLE_INACTIVE_REPO
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        result = asyncio.run(
            collector._fetch_coverage(mock_client, "MyPkg", "owner", "repo")
        )
        assert result["coverage_exists"] is False

    def test_404_returns_no_coverage(self, collector):
        mock_response = MagicMock()
        mock_response.status_code = 404

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        result = asyncio.run(
            collector._fetch_coverage(mock_client, "MyPkg", "owner", "repo")
        )
        assert result["coverage_exists"] is False

    def test_collect_invalid_url(self, collector):
        result = asyncio.run(
            collector.collect({"name": "Bad", "repo_url": "not-a-url"})
        )
        assert result["coverage_exists"] is False
        assert result["repository"] == "unknown"
