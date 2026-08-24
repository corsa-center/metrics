"""Unit tests for StaticAnalysisCollector."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from collectors.quality.static_analysis import StaticAnalysisCollector


@pytest.fixture
def collector():
    return StaticAnalysisCollector()


class TestEmptyResult:
    def test_structure(self, collector):
        result = collector._empty_result("MyPkg")
        assert result["package_name"] == "MyPkg"
        assert result["has_codeql"] is False
        assert result["workflow_file"] is None


class TestCollect:
    def test_collect_invalid_url(self, collector):
        result = asyncio.run(
            collector.collect({"name": "Bad", "repo_url": "not-a-url"})
        )
        assert result["has_codeql"] is False
        assert result["repository"] == "unknown"

    def test_finds_codeql_workflow(self, collector, monkeypatch):
        async def fake_check_file_exists(client, owner, repo, path):
            if path == ".github/workflows/codeql.yml":
                return f"https://github.com/{owner}/{repo}/blob/main/{path}"
            return None

        monkeypatch.setattr(collector, "_check_file_exists", fake_check_file_exists)

        result = asyncio.run(
            collector.collect({"name": "MyPkg", "repo_url": "https://github.com/owner/repo"})
        )
        assert result["has_codeql"] is True
        assert result["workflow_file"] == ".github/workflows/codeql.yml"
        assert result["workflow_url"]

    def test_no_codeql_workflow_found(self, collector, monkeypatch):
        async def fake_check_file_exists(client, owner, repo, path):
            return None

        monkeypatch.setattr(collector, "_check_file_exists", fake_check_file_exists)

        result = asyncio.run(
            collector.collect({"name": "MyPkg", "repo_url": "https://github.com/owner/repo"})
        )
        assert result["has_codeql"] is False
        assert result["workflow_file"] is None
