"""Unit tests for AccessibilityCollector."""

import asyncio
import pytest
from unittest.mock import patch
from collectors.quality.accessibility import AccessibilityCollector


@pytest.fixture
def collector():
    return AccessibilityCollector()


class TestEmptyResult:
    def test_structure(self, collector):
        result = collector._empty_result("MyPkg")
        assert result["package_name"] == "MyPkg"
        assert result["has_container"] is False
        assert result["has_portable_build_system"] is False
        assert result["categories"] == {}


class TestCollectInvalidUrl:
    def test_invalid_url_returns_empty(self, collector):
        result = asyncio.run(
            collector.collect({"name": "Bad", "repo_url": "not-a-url"})
        )
        assert result["has_container"] is False
        assert result["has_portable_build_system"] is False


class TestScan:
    def _run_scan(self, collector, found_paths):
        async def mock_exists(client, owner, repo, path):
            return path in found_paths

        async def run():
            import httpx
            async with httpx.AsyncClient() as client:
                with patch.object(collector, "_check_file_exists", side_effect=mock_exists):
                    return await collector._scan(client, "MyPkg", "owner", "repo")

        return asyncio.run(run())

    def test_dockerfile_only(self, collector):
        result = self._run_scan(collector, {"Dockerfile"})
        assert result["has_container"] is True
        assert result["categories"]["containers"]["found"] == ["Docker"]
        assert result["categories"]["containers"]["count_found"] == 1

    def test_cmake_and_dockerfile(self, collector):
        result = self._run_scan(collector, {"Dockerfile", "CMakeLists.txt"})
        assert result["has_container"] is True
        assert result["has_portable_build_system"] is True
        assert "CMake" in result["categories"]["build_systems"]["found"]

    def test_nothing_found(self, collector):
        result = self._run_scan(collector, set())
        assert result["has_container"] is False
        assert result["has_portable_build_system"] is False
        assert result["overall_score"]["percentage"] == 0.0

    def test_overall_score_increases_with_matches(self, collector):
        none = self._run_scan(collector, set())
        some = self._run_scan(collector, {"Dockerfile", "CMakeLists.txt", "pyproject.toml"})
        assert some["overall_score"]["percentage"] > none["overall_score"]["percentage"]

    def test_singularity_detected(self, collector):
        result = self._run_scan(collector, {"Singularity"})
        assert result["has_container"] is True
        assert "Singularity / Apptainer" in result["categories"]["containers"]["found"]

    def test_spack_detected(self, collector):
        result = self._run_scan(collector, {"package.py"})
        assert result["has_portable_build_system"] is True
        assert "Spack" in result["categories"]["build_systems"]["found"]
