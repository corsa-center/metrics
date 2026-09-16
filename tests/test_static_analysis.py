"""Unit tests for StaticAnalysisCollector."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from collectors.ecosystem.base import COLLECTION_GAP
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
        monkeypatch.setattr(collector, "_github_get", AsyncMock(return_value=None))

        result = asyncio.run(
            collector.collect({"name": "MyPkg", "repo_url": "https://github.com/owner/repo"})
        )
        assert result["has_codeql"] is False
        assert result["workflow_file"] is None
        assert "not_collected" not in result

    def test_filename_probe_gap_with_no_find_is_not_collected(self, collector, monkeypatch):
        monkeypatch.setattr(collector, "_check_file_exists", AsyncMock(return_value=COLLECTION_GAP))
        monkeypatch.setattr(collector, "_github_get", AsyncMock(return_value=None))

        result = asyncio.run(
            collector.collect({"name": "MyPkg", "repo_url": "https://github.com/owner/repo"})
        )
        assert result["has_codeql"] is False
        assert result["not_collected"] is True

    def test_found_workflow_survives_a_directory_listing_gap_elsewhere(self, collector, monkeypatch):
        # The direct filename probes find it before the fallback scan (which
        # would have gapped) is even reached.
        async def fake_check_file_exists(client, owner, repo, path):
            if path == ".github/workflows/codeql.yml":
                return f"https://github.com/{owner}/{repo}/blob/main/{path}"
            return None

        monkeypatch.setattr(collector, "_check_file_exists", fake_check_file_exists)
        monkeypatch.setattr(collector, "_github_get", AsyncMock(return_value=COLLECTION_GAP))

        result = asyncio.run(
            collector.collect({"name": "MyPkg", "repo_url": "https://github.com/owner/repo"})
        )
        assert result["has_codeql"] is True
        assert "not_collected" not in result


class TestScanWorkflowsForCodeqlGapHandling:
    def test_directory_listing_gap_is_tracked(self, collector):
        async def go():
            client = MagicMock()
            with patch.object(collector, "_github_get", new=AsyncMock(return_value=COLLECTION_GAP)):
                return await collector._scan_workflows_for_codeql(client, "o", "r")

        found, saw_gap = asyncio.run(go())
        assert found is None
        assert saw_gap is True

    def test_confirmed_no_workflows_dir_is_not_a_gap(self, collector):
        async def go():
            client = MagicMock()
            with patch.object(collector, "_github_get", new=AsyncMock(return_value=None)):
                return await collector._scan_workflows_for_codeql(client, "o", "r")

        found, saw_gap = asyncio.run(go())
        assert found is None
        assert saw_gap is False

    def test_content_fetch_failure_on_a_candidate_is_a_gap(self, collector):
        entries = [{"name": "ci.yml", "download_url": "http://x/ci.yml", "html_url": "http://h"}]

        async def go():
            client = AsyncMock()
            client.get = AsyncMock(side_effect=ConnectionError("boom"))
            with patch.object(collector, "_github_get", new=AsyncMock(return_value=entries)):
                return await collector._scan_workflows_for_codeql(client, "o", "r")

        found, saw_gap = asyncio.run(go())
        assert found is None
        assert saw_gap is True

    def test_match_found_despite_a_gap_on_another_candidate(self, collector):
        entries = [
            {"name": "broken.yml", "download_url": "http://x/broken.yml", "html_url": "http://b"},
            {"name": "codeql.yml", "download_url": "http://x/codeql.yml", "html_url": "http://c"},
        ]

        async def go():
            client = AsyncMock()

            async def fake_get(url, *a, **kw):
                resp = MagicMock()
                if "broken" in url:
                    raise ConnectionError("boom")
                resp.status_code = 200
                resp.text = "uses: github/codeql-action/analyze@v2"
                return resp

            client.get = fake_get
            with patch.object(collector, "_github_get", new=AsyncMock(return_value=entries)):
                return await collector._scan_workflows_for_codeql(client, "o", "r")

        found, saw_gap = asyncio.run(go())
        assert found is not None
        assert found["file"].endswith("codeql.yml")
