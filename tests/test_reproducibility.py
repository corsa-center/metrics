"""Unit tests for ReproducibilityCollector."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from collectors.ecosystem.base import COLLECTION_GAP
from collectors.quality.reproducibility import ReproducibilityCollector, _FILE_CHECKS


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
            "reproducibility_docs": {"found": ["INSTALL.md"], "percentage": 100.0},
            "semantic_versioning": {"uses_semver": True},
        }
        result = collector._compute_overall(categories)
        assert result["percentage"] == 100.0

    def test_nothing_present(self, collector):
        categories = {
            "containers":          {"found": [], "percentage": 0.0},
            "dependency_pinning":  {"found": [], "percentage": 0.0},
            "fair4rs_metadata":    {"found": [], "percentage": 0.0},
            "reproducibility_docs": {"found": [], "percentage": 0.0},
            "semantic_versioning": {"uses_semver": False},
        }
        result = collector._compute_overall(categories)
        assert result["percentage"] == 0.0

    def test_partial_score(self, collector):
        categories = {
            "containers":          {"found": [], "percentage": 0.0},
            "dependency_pinning":  {"found": [], "percentage": 0.0},
            "fair4rs_metadata":    {"found": ["CITATION.cff"], "percentage": 100.0},
            "reproducibility_docs": {"found": [], "percentage": 0.0},
            "semantic_versioning": {"uses_semver": True},
        }
        result = collector._compute_overall(categories)
        # fair4rs 0.20 * 100 + semver 0.15 * 100 = 35.0
        assert result["percentage"] == 35.0

    def test_missing_category_is_excluded_and_renormalized_not_scored_as_failure(self, collector):
        # A failed sub-scan must not take down the whole dimension, and must
        # not silently drag the score toward 0 either -- it's dropped from
        # the weighted blend and the remaining weight is renormalized.
        # If it had counted as 0% this would be 15.0 (0.15 * 100 / 1.0).
        result = collector._compute_overall({"semantic_versioning": {"uses_semver": True}})
        assert result["percentage"] == 100.0
        assert result["coverage"] == 0.15

    def test_semver_gap_is_excluded_from_blend(self, collector):
        categories = {
            "containers":          {"found": ["Docker"], "percentage": 100.0},
            "dependency_pinning":  {"found": [], "percentage": 0.0},
            "fair4rs_metadata":    {"found": [], "percentage": 0.0},
            "reproducibility_docs": {"found": [], "percentage": 0.0},
            "semantic_versioning": {"uses_semver": False, "not_collected": True},
        }
        result = collector._compute_overall(categories)
        # containers 0.20 * 100 / (1.0 - 0.15) = 23.53, not silently 20.0
        assert result["percentage"] == pytest.approx(23.53, abs=0.1)

    def test_file_category_gap_is_excluded_from_blend(self, collector):
        categories = {
            "containers":          {"found": [], "percentage": None},
            "dependency_pinning":  {"found": [], "percentage": 0.0},
            "fair4rs_metadata":    {"found": [], "percentage": 0.0},
            "reproducibility_docs": {"found": [], "percentage": 0.0},
            "semantic_versioning": {"uses_semver": True},
        }
        result = collector._compute_overall(categories)
        # semver 0.15 * 100 / (1.0 - 0.20) = 18.75, not silently 15.0
        assert result["percentage"] == pytest.approx(18.75, abs=0.1)

    def test_everything_gapped_reports_not_collected_status(self, collector):
        categories = {
            "containers":          {"found": [], "percentage": None},
            "dependency_pinning":  {"found": [], "percentage": None},
            "fair4rs_metadata":    {"found": [], "percentage": None},
            "reproducibility_docs": {"found": [], "percentage": None},
            "semantic_versioning": {"uses_semver": False, "not_collected": True},
        }
        result = collector._compute_overall(categories)
        assert result["percentage"] is None
        assert result["status"] == "not_collected"


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


class TestScanFilesGapHandling:
    def _run_scan(self, collector, responses):
        async def mock_exists(client, owner, repo, path):
            return responses.get(path, None)

        async def run():
            import httpx
            async with httpx.AsyncClient() as client:
                with patch.object(collector, "_check_file_exists", side_effect=mock_exists):
                    return await collector._scan_files(client, "owner", "repo")

        return asyncio.run(run())

    def test_gapped_item_is_not_collected_not_a_confirmed_miss(self, collector):
        result = self._run_scan(collector, {"Dockerfile": COLLECTION_GAP})
        containers = result["containers"]
        assert "Dockerfile" not in containers["missing"]
        assert "Dockerfile" in containers["not_collected"]

    def test_found_item_survives_a_gap_on_another_path(self, collector):
        result = self._run_scan(collector, {"poetry.lock": "http://x"})
        pinning = result["dependency_pinning"]
        assert "Poetry lock" in pinning["found"]

    def test_category_fully_gapped_reports_no_percentage(self, collector):
        # Every candidate for every item in fair4rs_metadata gaps.
        responses = {p: COLLECTION_GAP for paths in _FILE_CHECKS["fair4rs_metadata"].values() for p in paths}
        result = self._run_scan(collector, responses)
        assert result["fair4rs_metadata"]["percentage"] is None
        assert result["fair4rs_metadata"]["count_total"] == 0


class TestSemanticVersioningGapHandling:
    def _mock_client(self, status_code, body=None):
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        mock_resp.json.return_value = body
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        return mock_client

    def test_releases_request_failure_is_not_collected_not_a_confirmed_no(self, collector):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=ConnectionError("boom"))
        result = asyncio.run(collector._check_semantic_versioning(mock_client, "o", "r"))
        assert result["uses_semver"] is False
        assert result["not_collected"] is True

    def test_releases_rate_limited_is_not_collected(self, collector):
        client = self._mock_client(403)
        result = asyncio.run(collector._check_semantic_versioning(client, "o", "r"))
        assert result["not_collected"] is True

    def test_tags_request_failure_is_not_collected(self, collector):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=ConnectionError("boom"))
        result = asyncio.run(collector._check_tags(mock_client, "o", "r", 5))
        assert result["uses_semver"] is False
        assert result["not_collected"] is True

    def test_confirmed_no_releases_or_tags_is_a_real_negative(self, collector):
        # 200 with an empty body -- a real, trustworthy "no releases, no tags".
        empty_releases = self._mock_client(200, [])
        result = asyncio.run(collector._check_semantic_versioning(empty_releases, "o", "r"))
        assert result["uses_semver"] is False
        assert "not_collected" not in result
