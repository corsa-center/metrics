"""Unit tests for AccessibilityCollector."""

import asyncio
import pytest
from collectors.ecosystem.base import COLLECTION_GAP, RepoTree
from collectors.quality.accessibility import AccessibilityCollector


@pytest.fixture
def collector():
    return AccessibilityCollector()


def _tree(paths):
    return RepoTree("owner", "repo", list(paths), truncated=False)


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
    """_scan now takes a RepoTree (or COLLECTION_GAP) directly, rather than
    probing paths one at a time -- see METRIC_BLIND_SPOTS.md class F1/F2.
    """

    def test_dockerfile_only(self, collector):
        result = collector._scan(_tree({"Dockerfile"}), "MyPkg", "owner", "repo")
        assert result["has_container"] is True
        assert result["categories"]["containers"]["found"] == ["Docker"]
        assert result["categories"]["containers"]["count_found"] == 1

    def test_cmake_and_dockerfile(self, collector):
        result = collector._scan(_tree({"Dockerfile", "CMakeLists.txt"}), "MyPkg", "owner", "repo")
        assert result["has_container"] is True
        assert result["has_portable_build_system"] is True
        assert "CMake" in result["categories"]["build_systems"]["found"]

    def test_nothing_found(self, collector):
        result = collector._scan(_tree(set()), "MyPkg", "owner", "repo")
        assert result["has_container"] is False
        assert result["has_portable_build_system"] is False
        assert result["overall_score"]["percentage"] == 0.0

    def test_overall_score_increases_with_matches(self, collector):
        none = collector._scan(_tree(set()), "MyPkg", "owner", "repo")
        some = collector._scan(
            _tree({"Dockerfile", "CMakeLists.txt", "pyproject.toml"}), "MyPkg", "owner", "repo"
        )
        assert some["overall_score"]["percentage"] > none["overall_score"]["percentage"]

    def test_singularity_detected(self, collector):
        result = collector._scan(_tree({"Singularity"}), "MyPkg", "owner", "repo")
        assert result["has_container"] is True
        assert "Singularity / Apptainer" in result["categories"]["containers"]["found"]

    def test_spack_detected(self, collector):
        result = collector._scan(_tree({"package.py"}), "MyPkg", "owner", "repo")
        assert result["has_portable_build_system"] is True
        assert "Spack" in result["categories"]["build_systems"]["found"]

    def test_gnumakefile_in_template_detected(self, collector):
        # AMReX-Codes/amrex ships GNUmakefile.in (a template for its custom
        # GNU Make build) rather than a literal GNUmakefile/Makefile.
        result = collector._scan(_tree({"GNUmakefile.in"}), "MyPkg", "owner", "repo")
        assert result["has_portable_build_system"] is True
        assert "Makefile" in result["categories"]["build_systems"]["found"]

    def test_makefile_am_detected(self, collector):
        # open-mpi/ompi, pmodels/mpich and four other portfolio repos build
        # with GNU Autotools and ship Makefile.am, not Makefile/GNUmakefile.
        result = collector._scan(_tree({"Makefile.am"}), "MyPkg", "owner", "repo")
        assert "Makefile" in result["categories"]["build_systems"]["found"]

    def test_match_is_case_insensitive(self, collector):
        # superlu ships DOC/CMakeLists.txt-style capitalization elsewhere in
        # the portfolio; confirm a differently-cased CMakeLists.txt matches.
        result = collector._scan(_tree({"cmakelists.txt"}), "MyPkg", "owner", "repo")
        assert "CMake" in result["categories"]["build_systems"]["found"]


class TestScanGapHandling:
    def test_gapped_tree_is_not_collected_not_a_confirmed_missing(self, collector):
        result = collector._scan(COLLECTION_GAP, "MyPkg", "owner", "repo")
        containers = result["categories"]["containers"]
        assert containers["missing"] == []
        assert "Docker" in containers["not_collected"]

    def test_confirmed_missing_is_not_the_same_as_gapped(self, collector):
        result = collector._scan(_tree({"README.md"}), "MyPkg", "owner", "repo")
        containers = result["categories"]["containers"]
        assert containers["not_collected"] == []
        assert "Docker" in containers["missing"]

    def test_found_item_survives_confirmed_misses_on_siblings(self, collector):
        result = collector._scan(_tree({"CMakeLists.txt"}), "MyPkg", "owner", "repo")
        assert "CMake" in result["categories"]["build_systems"]["found"]

    def test_category_fully_gapped_reports_no_percentage(self, collector):
        result = collector._scan(COLLECTION_GAP, "MyPkg", "owner", "repo")
        install_docs = result["categories"]["install_docs"]
        assert install_docs["percentage"] is None
        assert install_docs["count_total"] == 0

    def test_everything_gapped_reports_not_collected_overall(self, collector):
        result = collector._scan(COLLECTION_GAP, "MyPkg", "owner", "repo")
        assert result["overall_score"]["score"] is None
        assert result["overall_score"]["max_score"] == 0
        assert result["overall_score"]["percentage"] is None
        assert result["overall_score"]["status"] == "not_collected"


class TestContainersAnywhereInTree:
    def test_dockerfile_in_a_subdirectory_counts(self):
        tree = RepoTree("o", "r", ["scripts/docker/Dockerfile", "CMakeLists.txt"], truncated=False)
        out = AccessibilityCollector()._scan(tree, "r", "o", "r")
        assert out["has_container"] is True

    def test_vendored_dockerfile_does_not_count(self):
        tree = RepoTree("o", "r", ["extern/tool/Dockerfile", "CMakeLists.txt"], truncated=False)
        assert AccessibilityCollector()._scan(tree, "r", "o", "r")["has_container"] is False
