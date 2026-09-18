"""Unit tests for SupplyChainCollector (CASS Section 4.3.8)."""

import asyncio
import pytest

from forge.base import COLLECTION_GAP
from collectors.quality.supply_chain import SupplyChainCollector, _SBOM_ROOT_FILES


class FakeForge:
    """Minimal stand-in for GitHubForge/GitLabForge."""

    def __init__(self):
        self.file_results = {}
        self.releases_result = []

    def extract_ref(self, repo_url):
        return None if repo_url == "not-a-url" else "o/r"

    async def file_exists(self, client, ref, path):
        return self.file_results.get(path)

    async def releases(self, client, ref, *, per_page=30, page=1):
        return self.releases_result

    def get_timestamp(self):
        return "2026-01-01T00:00:00+00:00"


@pytest.fixture
def forge():
    return FakeForge()


@pytest.fixture
def collector(forge):
    return SupplyChainCollector(forge)


class TestEmptyResult:
    def test_structure(self, collector):
        result = collector._empty_result("MyPkg")
        assert result["package_name"] == "MyPkg"
        assert result["has_sbom"] is False
        assert result["has_build_provenance"] is False
        assert result["overall_score"]["max_score"] == 2
        assert result["sub_metrics"]["dependency_vulnerability_posture"]["not_collected"] is True
        assert result["sub_metrics"]["dependency_freshness"]["not_collected"] is True


class TestCollectInvalidUrl:
    def test_invalid_url_returns_empty(self, collector):
        result = asyncio.run(
            collector.collect({"name": "Bad", "repo_url": "not-a-url"})
        )
        assert result["has_sbom"] is False
        assert result["repository"] == "unknown"


class TestFindSbom:
    def test_root_file_wins(self, collector):
        result = collector._find_sbom("https://github.com/o/r/blob/main/sbom.spdx.json", [])
        assert result["passing"] is True
        assert result["value"] == "Committed to repository"

    def test_release_asset_match(self, collector):
        assets = [{"name": "app-v1.0.0.sbom.spdx.json", "url": "http://x", "release": "v1.0.0"}]
        result = collector._find_sbom(None, assets)
        assert result["passing"] is True
        assert "v1.0.0" in result["value"]

    def test_cyclonedx_asset_match(self, collector):
        assets = [{"name": "bom.cdx.json", "url": "http://x", "release": "v2.0.0"}]
        result = collector._find_sbom(None, assets)
        assert result["passing"] is True

    def test_no_match(self, collector):
        assets = [{"name": "checksums.txt", "url": "http://x", "release": "v1.0.0"}]
        result = collector._find_sbom(None, assets)
        assert result["passing"] is False
        assert result["value"] == "No SBOM found"

    def test_unrelated_filename_is_not_a_false_positive(self, collector):
        # "bom.xml" hint shouldn't match an unrelated file like "abomination.txt"
        assets = [{"name": "abomination.txt", "url": "http://x", "release": "v1"}]
        result = collector._find_sbom(None, assets)
        assert result["passing"] is False

    def test_no_match_under_gap_is_not_collected_not_a_negative(self, collector):
        result = collector._find_sbom(None, [], has_gap=True)
        assert result["passing"] is False
        assert result["not_collected"] is True

    def test_positive_match_survives_a_gap_elsewhere(self, collector):
        # Found in a release asset despite has_gap=True (e.g. the root
        # check gapped but the release-assets fetch succeeded) -- still real.
        assets = [{"name": "sbom.spdx.json", "url": "http://x", "release": "v1"}]
        result = collector._find_sbom(None, assets, has_gap=True)
        assert result["passing"] is True
        assert "not_collected" not in result


class TestFindProvenance:
    def test_intoto_attestation_match(self, collector):
        assets = [{"name": "multiple.intoto.jsonl", "url": "http://x", "release": "v1.0.0"}]
        result = collector._find_provenance(assets)
        assert result["passing"] is True

    def test_slsa_match(self, collector):
        assets = [{"name": "slsa-provenance.json", "url": "http://x", "release": "v1.0.0"}]
        result = collector._find_provenance(assets)
        assert result["passing"] is True

    def test_no_match(self, collector):
        assets = [{"name": "release.tar.gz", "url": "http://x", "release": "v1.0.0"}]
        result = collector._find_provenance(assets)
        assert result["passing"] is False
        assert result["value"] == "No build provenance found"

    def test_no_match_under_gap_is_not_collected_not_a_negative(self, collector):
        result = collector._find_provenance([], has_gap=True)
        assert result["passing"] is False
        assert result["not_collected"] is True


class TestComputeOverall:
    def test_both_passing(self, collector):
        sub = {
            "sbom_detection": {"passing": True},
            "build_provenance": {"passing": True},
            "dependency_vulnerability_posture": {"passing": False, "not_collected": True},
            "dependency_freshness": {"passing": False, "not_collected": True},
        }
        overall = collector._compute_overall(sub)
        assert overall == {"score": 2, "max_score": 2, "percentage": 100.0}

    def test_not_collected_excluded_from_max_score(self, collector):
        sub = {
            "sbom_detection": {"passing": False},
            "build_provenance": {"passing": False},
            "dependency_vulnerability_posture": {"passing": False, "not_collected": True},
            "dependency_freshness": {"passing": False, "not_collected": True},
        }
        overall = collector._compute_overall(sub)
        assert overall["max_score"] == 2
        assert overall["score"] == 0

    def test_all_not_collected_gives_zero_not_error(self, collector):
        sub = {"x": {"passing": False, "not_collected": True}}
        overall = collector._compute_overall(sub)
        assert overall == {"score": 0, "max_score": 0, "percentage": 0.0}


class TestCheckRootSbom:
    def test_found(self, collector, forge):
        forge.file_results = {"sbom.spdx.json": "https://github.com/o/r/blob/main/sbom.spdx.json"}
        url, saw_gap = asyncio.run(collector._check_root_sbom(None, "o/r"))
        assert url is not None
        assert saw_gap is False

    def test_not_found(self, collector):
        url, saw_gap = asyncio.run(collector._check_root_sbom(None, "o/r"))
        assert url is None
        assert saw_gap is False

    def test_gap_on_one_path_is_tracked_even_if_a_later_path_is_confirmed_absent(self, collector, forge):
        forge.file_results = {_SBOM_ROOT_FILES[0]: COLLECTION_GAP}
        url, saw_gap = asyncio.run(collector._check_root_sbom(None, "o/r"))
        assert url is None
        assert saw_gap is True


class TestFetchReleaseAssets:
    def test_flattens_assets_across_releases(self, collector, forge):
        forge.releases_result = [
            {"tag_name": "v2.0.0", "assets": [{"name": "a.tar.gz", "browser_download_url": "u1"}]},
            {"tag_name": "v1.0.0", "assets": [{"name": "sbom.json", "browser_download_url": "u2"}]},
        ]
        assets, is_gap = asyncio.run(collector._fetch_release_assets(None, "o/r"))
        assert len(assets) == 2
        assert assets[1]["release"] == "v1.0.0"
        assert is_gap is False

    def test_no_assets(self, collector, forge):
        forge.releases_result = [{"tag_name": "v1.0.0", "assets": []}]
        assets, is_gap = asyncio.run(collector._fetch_release_assets(None, "o/r"))
        assert assets == []
        assert is_gap is False

    def test_confirmed_no_releases_is_a_real_empty_list_not_a_gap(self, collector, forge):
        forge.releases_result = []
        assets, is_gap = asyncio.run(collector._fetch_release_assets(None, "o/r"))
        assert assets == []
        assert is_gap is False

    def test_gap_is_a_gap_not_a_confirmed_empty_list(self, collector, forge):
        forge.releases_result = COLLECTION_GAP
        assets, is_gap = asyncio.run(collector._fetch_release_assets(None, "o/r"))
        assert assets == []
        assert is_gap is True
