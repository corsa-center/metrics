"""Unit tests for AccessibilityCollector."""

import asyncio
import pytest
from forge.base import COLLECTION_GAP
from collectors.quality.accessibility import AccessibilityCollector


class FakeForge:
    """Minimal stand-in for GitHubForge/GitLabForge."""

    def __init__(self):
        self.file_results = {}

    def extract_ref(self, repo_url):
        return None if repo_url == "not-a-url" else "o/r"

    async def file_exists(self, client, ref, path):
        return self.file_results.get(path)

    def get_timestamp(self):
        return "2026-01-01T00:00:00+00:00"


@pytest.fixture
def forge():
    return FakeForge()


@pytest.fixture
def collector(forge):
    return AccessibilityCollector(forge)


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
    def _run_scan(self, collector, forge, found_paths):
        forge.file_results = {p: "http://x" for p in found_paths}
        return asyncio.run(collector._scan(None, "MyPkg", "o/r"))

    def test_dockerfile_only(self, collector, forge):
        result = self._run_scan(collector, forge, {"Dockerfile"})
        assert result["has_container"] is True
        assert result["categories"]["containers"]["found"] == ["Docker"]
        assert result["categories"]["containers"]["count_found"] == 1

    def test_cmake_and_dockerfile(self, collector, forge):
        result = self._run_scan(collector, forge, {"Dockerfile", "CMakeLists.txt"})
        assert result["has_container"] is True
        assert result["has_portable_build_system"] is True
        assert "CMake" in result["categories"]["build_systems"]["found"]

    def test_nothing_found(self, collector, forge):
        result = self._run_scan(collector, forge, set())
        assert result["has_container"] is False
        assert result["has_portable_build_system"] is False
        assert result["overall_score"]["percentage"] == 0.0

    def test_overall_score_increases_with_matches(self, collector, forge):
        none = self._run_scan(collector, forge, set())
        some = self._run_scan(collector, forge, {"Dockerfile", "CMakeLists.txt", "pyproject.toml"})
        assert some["overall_score"]["percentage"] > none["overall_score"]["percentage"]

    def test_singularity_detected(self, collector, forge):
        result = self._run_scan(collector, forge, {"Singularity"})
        assert result["has_container"] is True
        assert "Singularity / Apptainer" in result["categories"]["containers"]["found"]

    def test_spack_detected(self, collector, forge):
        result = self._run_scan(collector, forge, {"package.py"})
        assert result["has_portable_build_system"] is True
        assert "Spack" in result["categories"]["build_systems"]["found"]


class TestScanGapHandling:
    def _run_scan(self, collector, forge, responses):
        forge.file_results = responses
        return asyncio.run(collector._scan(None, "MyPkg", "o/r"))

    def test_gapped_item_is_not_collected_not_a_confirmed_missing(self, collector, forge):
        result = self._run_scan(collector, forge, {"Dockerfile": COLLECTION_GAP})
        containers = result["categories"]["containers"]
        assert "Docker" not in containers["missing"]
        assert "Docker" in containers["not_collected"]

    def test_found_item_survives_a_gap_on_a_sibling_candidate(self, collector, forge):
        result = self._run_scan(collector, forge, {"CMakeLists.txt": "http://x"})
        assert "CMake" in result["categories"]["build_systems"]["found"]

    def test_category_fully_gapped_reports_no_percentage(self, collector, forge):
        responses = {"INSTALL": COLLECTION_GAP, "INSTALL.md": COLLECTION_GAP,
                     "INSTALL.rst": COLLECTION_GAP, "INSTALL.txt": COLLECTION_GAP}
        result = self._run_scan(collector, forge, responses)
        install_docs = result["categories"]["install_docs"]
        assert install_docs["percentage"] is None
        assert install_docs["count_total"] == 0

    def test_everything_gapped_reports_not_collected_overall(self, collector, forge):
        all_paths = {p for items in [
            "Dockerfile", "docker/Dockerfile", ".docker/Dockerfile",
            "Singularity", "singularity/Singularity", "Apptainer", "apptainer/Apptainer", "*.def",
            "CMakeLists.txt", "package.py", "spack/package.py",
            "meta.yaml", "conda/meta.yaml", "recipe/meta.yaml", "environment.yml", "environment.yaml",
            "configure.ac", "configure.in", "Makefile", "makefile", "GNUmakefile",
            "pyproject.toml", "setup.py", "setup.cfg",
            "INSTALL", "INSTALL.md", "INSTALL.rst", "INSTALL.txt",
        ] for p in [items]}
        responses = {p: COLLECTION_GAP for p in all_paths}
        result = self._run_scan(collector, forge, responses)
        assert result["overall_score"]["score"] is None
        assert result["overall_score"]["max_score"] == 0
        assert result["overall_score"]["percentage"] is None
        assert result["overall_score"]["status"] == "not_collected"
