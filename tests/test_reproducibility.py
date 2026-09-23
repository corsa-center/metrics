"""Unit tests for ReproducibilityCollector."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from collectors.ecosystem.base import COLLECTION_GAP, RepoTree
from collectors.quality.reproducibility import (
    ReproducibilityCollector, _FILE_CHECKS, _versioning_scheme,
)


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

    def test_calendar_versioning_counts(self, collector):
        # AMReX tags 26.09 monthly on schedule; strict three-component semver
        # read that as no versioning discipline. corsa-center/metrics#53.
        client = self._mock_releases(["26.09", "26.08", "26.07"])
        result = asyncio.run(
            collector._check_semantic_versioning(client, "owner", "repo")
        )
        assert result["uses_semver"] is True
        assert result["semver_count"] == 3
        assert result["scheme"] == "calver"

    def test_four_digit_calendar_versioning_counts(self, collector):
        client = self._mock_releases(["2024.05", "2024.02"])
        result = asyncio.run(
            collector._check_semantic_versioning(client, "owner", "repo")
        )
        assert result["uses_semver"] is True
        assert result["scheme"] == "calver"

    def test_semver_still_reports_as_semver(self, collector):
        client = self._mock_releases(["v1.2.3"])
        result = asyncio.run(
            collector._check_semantic_versioning(client, "owner", "repo")
        )
        assert result["scheme"] == "semver"


class TestVersioningSchemeNormalization:
    """A project-name-prefixed tag reads the same as a bare version string
    once normalized -- see corsa-center/metrics#53 (Version Control Best
    Practices) and METRIC_BLIND_SPOTS.md class F6. Each case here is a real
    tag from a specific portfolio repo the probe flagged as still failing
    after the initial CalVer fix.
    """

    @pytest.mark.parametrize("tag,expected", [
        ("llvmorg-23.1.1", "semver"),                # llvm/llvm-project
        ("trilinos-release-17-2-1", "semver"),        # trilinos/Trilinos (hyphens, not dots)
        ("papi-7-2-0-t", "semver"),                   # icl-utk-edu/papi (trailing suffix)
        ("legion-26.06.0", "semver"),                 # StanfordLegion/legion
        ("gex-2025.8.0", "semver"),                   # BerkeleyLab/gasnet
        ("upcxx-2025.10.0", "semver"),                # BerkeleyLab/upcxx
        ("vstable_2026_09_04", "semver"),              # snl-dakota/dakota (underscores)
        ("release-2022.05.15", "semver"),             # HPCToolkit/hpctoolkit
        ("release-2022.04", "calver"),                # HPCToolkit/hpctoolkit (2-part)
        ("tag.v1.10.0", "semver"),                    # Parallel-NetCDF/PnetCDF
        ("checkpoint.1.15.1", "semver"),              # Parallel-NetCDF/PnetCDF
        ("flang_20190329", "calver"),                 # flang-compiler/flang (compact date)
        ("v3.0", "major.minor"),                      # OpenACCUserGroup/OpenACCV-V
        ("v7.0", "major.minor"),                      # CODARcode/Chimbuko
        ("v0.31", "major.minor"),                     # SCOREC/pumi-pic
        ("26.09", "calver"),                          # AMReX-Codes/amrex
        ("v1.2.3", "semver"),
    ])
    def test_real_portfolio_tags_now_recognized(self, tag, expected):
        assert _versioning_scheme(tag) == expected

    @pytest.mark.parametrize("tag", [
        "main", "nightly", "latest", "gex-stable",
        "urp_rs_21", "Old_master_support_end",
    ])
    def test_non_version_tags_still_unmatched(self, tag):
        assert _versioning_scheme(tag) is None

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

    def test_non_matching_releases_do_not_fall_back_to_tags(self, collector):
        # Releases exist but don't carry a real version -- reported as-is.
        # A project's raw git tags include every ad hoc marker it ever made
        # (support-end notices, downstream collaboration snapshots, ...),
        # not just its versioning history, so they aren't a reliable
        # fallback source the way "no releases at all" is: sandialabs/Albany
        # publishes exactly one non-versioned Release, and its tags are
        # dominated by tags like "compass-2026-03-21" for an external
        # collaboration's snapshots -- unrelated to Albany's own versioning
        # discipline, but shaped enough like a date to be misread as one.
        release_resp = MagicMock()
        release_resp.status_code = 200
        release_resp.json.return_value = [{"tag_name": "Initial release"}]
        release_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=release_resp)

        result = asyncio.run(
            collector._check_semantic_versioning(mock_client, "owner", "repo")
        )
        assert result["uses_semver"] is False
        assert result["example_tags"] == ["Initial release"]
        mock_client.get.assert_called_once()


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
    """_scan_files now takes a RepoTree (or COLLECTION_GAP) directly, rather
    than probing paths one at a time -- see METRIC_BLIND_SPOTS.md class F2.
    """

    def _tree(self, paths):
        return RepoTree("owner", "repo", list(paths), truncated=False)

    def test_dockerfile_detected(self, collector):
        result = collector._scan_files(self._tree({"Dockerfile"}))
        assert "Dockerfile" in result["containers"]["found"]

    def test_poetry_lock_detected(self, collector):
        result = collector._scan_files(self._tree({"poetry.lock"}))
        assert "Poetry lock" in result["dependency_pinning"]["found"]

    def test_citation_cff_detected(self, collector):
        result = collector._scan_files(self._tree({"CITATION.cff"}))
        assert "CITATION.cff" in result["fair4rs_metadata"]["found"]

    def test_match_is_case_insensitive(self, collector):
        result = collector._scan_files(self._tree({"Poetry.Lock"}))
        assert "Poetry lock" in result["dependency_pinning"]["found"]

    def test_nothing_found(self, collector):
        result = collector._scan_files(self._tree(set()))
        for cat in ("containers", "dependency_pinning", "fair4rs_metadata"):
            assert result[cat]["found"] == []
            assert result[cat]["percentage"] == 0.0


class TestScanFilesGapHandling:
    def test_gapped_tree_reports_every_item_not_collected(self, collector):
        result = collector._scan_files(COLLECTION_GAP)
        containers = result["containers"]
        assert containers["found"] == []
        assert containers["missing"] == []
        assert set(containers["not_collected"]) == set(_FILE_CHECKS["containers"])

    def test_category_fully_gapped_reports_no_percentage(self, collector):
        result = collector._scan_files(COLLECTION_GAP)
        assert result["fair4rs_metadata"]["percentage"] is None
        assert result["fair4rs_metadata"]["count_total"] == 0

    def test_confirmed_missing_is_not_the_same_as_gapped(self, collector):
        tree = RepoTree("owner", "repo", ["README.md"], truncated=False)
        result = collector._scan_files(tree)
        containers = result["containers"]
        assert containers["not_collected"] == []
        assert set(containers["missing"]) == set(_FILE_CHECKS["containers"])


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
