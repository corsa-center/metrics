"""Unit tests for ReproducibilityCollector."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from collectors.quality.reproducibility import ReproducibilityCollector


@pytest.fixture
def collector():
    return ReproducibilityCollector()


class TestEmptyResult:
    def test_structure(self, collector):
        result = collector._empty_result("MyPkg")
        assert result["package_name"] == "MyPkg"
        assert result["has_container"] is False
        assert result["has_dependency_pinning"] is False
        assert result["has_fair4rs_metadata"] is False
        assert result["uses_semantic_versioning"] is False
        assert result["overall_score"]["percentage"] == 0.0


class TestCollectInvalidUrl:
    def test_invalid_url_returns_empty(self, collector):
        result = asyncio.run(
            collector.collect({"name": "Bad", "repo_url": "not-a-url"})
        )
        assert result["has_container"] is False
        assert result["repository"] == "unknown"


class TestSemanticVersioning:
    def _mock_releases(self, tags):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"tag_name": t} for t in tags]
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        return mock_client

    def test_semver_tags_detected(self, collector):
        client = self._mock_releases(["v1.2.3", "v1.2.2", "v1.2.1"])
        result = asyncio.run(
            collector._check_semantic_versioning(client, "owner", "repo")
        )
        assert result["uses_semver"] is True
        assert result["semver_count"] == 3

    def test_non_semver_tags(self, collector):
        client = self._mock_releases(["release-2024", "latest", "nightly"])
        result = asyncio.run(
            collector._check_semantic_versioning(client, "owner", "repo")
        )
        assert result["uses_semver"] is False
        assert result["semver_count"] == 0

    def test_mixed_tags(self, collector):
        client = self._mock_releases(["v2.0.0", "nightly", "v1.9.0"])
        result = asyncio.run(
            collector._check_semantic_versioning(client, "owner", "repo")
        )
        assert result["uses_semver"] is True
        assert result["semver_count"] == 2

    def test_no_releases_falls_back_to_tags(self, collector):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = []
        mock_resp.raise_for_status = MagicMock()

        tag_resp = MagicMock()
        tag_resp.status_code = 200
        tag_resp.json.return_value = [{"name": "v3.0.0"}]
        tag_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[mock_resp, tag_resp])

        result = asyncio.run(
            collector._check_semantic_versioning(mock_client, "owner", "repo")
        )
        assert result["uses_semver"] is True


class TestComputeOverall:
    def test_all_present(self, collector):
        categories = {
            "containers":          {"found": ["Docker"], "percentage": 100.0},
            "dependency_pinning":  {"found": ["poetry.lock"], "percentage": 100.0},
            "fair4rs_metadata":    {"found": ["CITATION.cff"], "percentage": 100.0},
            "semantic_versioning": {"uses_semver": True},
        }
        result = collector._compute_overall(categories)
        assert result["percentage"] == 100.0

    def test_nothing_present(self, collector):
        categories = {
            "containers":          {"found": [], "percentage": 0.0},
            "dependency_pinning":  {"found": [], "percentage": 0.0},
            "fair4rs_metadata":    {"found": [], "percentage": 0.0},
            "semantic_versioning": {"uses_semver": False},
        }
        result = collector._compute_overall(categories)
        assert result["percentage"] == 0.0

    def test_partial_score(self, collector):
        categories = {
            "containers":          {"found": [], "percentage": 0.0},
            "dependency_pinning":  {"found": [], "percentage": 0.0},
            "fair4rs_metadata":    {"found": ["CITATION.cff"], "percentage": 100.0},
            "semantic_versioning": {"uses_semver": True},
        }
        result = collector._compute_overall(categories)
        # fair4rs 0.25 * 100 + semver 0.15 * 100 = 40.0
        assert result["percentage"] == 40.0


class TestScanFiles:
    def _run_scan(self, collector, found_paths):
        async def mock_exists(client, owner, repo, path):
            return path in found_paths

        async def run():
            import httpx
            async with httpx.AsyncClient() as client:
                with patch.object(collector, "_check_file_exists", side_effect=mock_exists):
                    return await collector._scan_files(client, "owner", "repo")

        return asyncio.run(run())

    def test_dockerfile_detected(self, collector):
        result = self._run_scan(collector, {"Dockerfile"})
        assert "Dockerfile" in result["containers"]["found"]

    def test_poetry_lock_detected(self, collector):
        result = self._run_scan(collector, {"poetry.lock"})
        assert "Poetry lock" in result["dependency_pinning"]["found"]

    def test_citation_cff_detected(self, collector):
        result = self._run_scan(collector, {"CITATION.cff"})
        assert "CITATION.cff" in result["fair4rs_metadata"]["found"]

    def test_nothing_found(self, collector):
        result = self._run_scan(collector, set())
        for cat in ("containers", "dependency_pinning", "fair4rs_metadata"):
            assert result[cat]["found"] == []
            assert result[cat]["percentage"] == 0.0
