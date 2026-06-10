"""Unit tests for OpenSSFScorecardCollector."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from collectors.sustainability.openssf_scorecard import OpenSSFScorecardCollector


@pytest.fixture
def collector():
    return OpenSSFScorecardCollector()


SAMPLE_SCORECARD = {
    "score": 7.3,
    "checks": [
        {"name": "Branch-Protection", "score": 8, "reason": "...", "documentation": {"url": "https://example.com"}},
        {"name": "CI-Tests",          "score": 10, "reason": "...", "documentation": {"url": "https://example.com"}},
        {"name": "Code-Review",       "score": 3, "reason": "...", "documentation": {"url": "https://example.com"}},
        {"name": "Dependency-Update-Tool", "score": 0, "reason": "...", "documentation": {"url": ""}},
    ],
}


class TestEmptyResult:
    def test_structure(self, collector):
        result = collector._empty_result("MyPkg")
        assert result["package_name"] == "MyPkg"
        assert result["scorecard_exists"] is False
        assert result["score"] is None
        assert result["checks"] == {}


class TestNoScorecardResult:
    def test_structure(self, collector):
        result = collector._no_scorecard_result("MyPkg", "owner", "repo")
        assert result["scorecard_exists"] is False
        assert result["score"] is None
        assert "recommendation" in result


class TestFetchScorecard:
    def test_successful_fetch(self, collector):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = SAMPLE_SCORECARD
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        result = asyncio.run(
            collector._fetch_scorecard(mock_client, "MyPkg", "owner", "repo")
        )

        assert result["scorecard_exists"] is True
        assert result["score"] == 7.3
        assert result["percentage"] == 73.0
        assert result["checks_total"] == 4
        # checks with score >= 7: Branch-Protection (8) and CI-Tests (10) = 2
        assert result["checks_passed"] == 2
        assert "Branch-Protection" in result["checks"]

    def test_404_returns_no_scorecard(self, collector):
        mock_response = MagicMock()
        mock_response.status_code = 404

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        result = asyncio.run(
            collector._fetch_scorecard(mock_client, "MyPkg", "owner", "repo")
        )
        assert result["scorecard_exists"] is False
        assert "recommendation" in result

    def test_collect_invalid_url(self, collector):
        result = asyncio.run(
            collector.collect({"name": "Bad", "repo_url": "not-a-url"})
        )
        assert result["scorecard_exists"] is False
        assert result["repository"] == "unknown"
