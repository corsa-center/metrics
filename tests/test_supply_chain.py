"""Unit tests for SupplyChainCollector (CASS Section 4.3.8)."""

import asyncio
import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from collectors.ecosystem.base import COLLECTION_GAP
from collectors.quality.supply_chain import SupplyChainCollector, _SBOM_ROOT_FILES


@pytest.fixture
def collector():
    return SupplyChainCollector()


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
    def test_found(self, collector):
        async def mock_exists(client, owner, repo, path):
            return "https://github.com/o/r/blob/main/sbom.spdx.json" if path == "sbom.spdx.json" else None

        async def run():
            async with httpx.AsyncClient() as client:
                with patch.object(collector, "_check_file_exists", side_effect=mock_exists):
                    return await collector._check_root_sbom(client, "o", "r")

        url, saw_gap = asyncio.run(run())
        assert url is not None
        assert saw_gap is False

    def test_not_found(self, collector):
        async def mock_exists(client, owner, repo, path):
            return None

        async def run():
            async with httpx.AsyncClient() as client:
                with patch.object(collector, "_check_file_exists", side_effect=mock_exists):
                    return await collector._check_root_sbom(client, "o", "r")

        url, saw_gap = asyncio.run(run())
        assert url is None
        assert saw_gap is False

    def test_gap_on_one_path_is_tracked_even_if_a_later_path_is_confirmed_absent(self, collector):
        async def mock_exists(client, owner, repo, path):
            return COLLECTION_GAP if path == _SBOM_ROOT_FILES[0] else None

        async def run():
            async with httpx.AsyncClient() as client:
                with patch.object(collector, "_check_file_exists", side_effect=mock_exists):
                    return await collector._check_root_sbom(client, "o", "r")

        url, saw_gap = asyncio.run(run())
        assert url is None
        assert saw_gap is True


class TestFetchReleaseAssets:
    def _mock_client(self, status_code, body=None):
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        mock_resp.json.return_value = body
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        return mock_client

    def test_flattens_assets_across_releases(self, collector):
        releases = [
            {"tag_name": "v2.0.0", "assets": [{"name": "a.tar.gz", "browser_download_url": "u1"}]},
            {"tag_name": "v1.0.0", "assets": [{"name": "sbom.json", "browser_download_url": "u2"}]},
        ]
        client = self._mock_client(200, releases)
        assets, is_gap = asyncio.run(collector._fetch_release_assets(client, "o", "r"))
        assert len(assets) == 2
        assert assets[1]["release"] == "v1.0.0"
        assert is_gap is False

    def test_no_assets(self, collector):
        client = self._mock_client(200, [{"tag_name": "v1.0.0", "assets": []}])
        assets, is_gap = asyncio.run(collector._fetch_release_assets(client, "o", "r"))
        assert assets == []
        assert is_gap is False

    def test_confirmed_404_is_a_real_empty_list_not_a_gap(self, collector):
        # A repo with no releases at all -- a real, trustworthy result.
        client = self._mock_client(404)
        assets, is_gap = asyncio.run(collector._fetch_release_assets(client, "o", "r"))
        assert assets == []
        assert is_gap is False

    def test_request_failure_is_a_gap_not_a_confirmed_empty_list(self, collector):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("boom"))
        assets, is_gap = asyncio.run(collector._fetch_release_assets(mock_client, "o", "r"))
        assert assets == []
        assert is_gap is True

    def test_rate_limited_after_retries_is_a_gap(self, collector):
        client = self._mock_client(403)
        assets, is_gap = asyncio.run(collector._fetch_release_assets(client, "o", "r"))
        assert assets == []
        assert is_gap is True
