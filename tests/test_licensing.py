"""Unit tests for LicensingCollector."""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from forge.base import COLLECTION_GAP
from collectors.ecosystem.licensing import LicensingCollector


class FakeForge:
    """Minimal stand-in for GitHubForge/GitLabForge."""

    def __init__(self):
        self.license_result = None
        self.file_metadata_results = {}

    def extract_ref(self, repo_url):
        return None if repo_url == "not-a-url" else "o/r"

    async def license(self, client, ref):
        return self.license_result

    async def file_metadata(self, client, ref, path):
        return self.file_metadata_results.get(path)

    def get_timestamp(self):
        return "2026-01-01T00:00:00+00:00"


@pytest.fixture
def forge():
    return FakeForge()


@pytest.fixture
def collector(forge):
    return LicensingCollector(forge)


class TestAnalyzeLicense:
    def test_known_spdx_id_from_api(self, collector):
        info = {"found": True, "spdx_id": "MIT", "license_key": "mit", "license_name": "MIT License"}
        result = collector._analyze_license(info)
        assert result["osi_approved"] is True
        assert result["category"] == "Permissive"

    def test_not_found_reports_no_license(self, collector):
        result = collector._analyze_license({"found": False})
        assert result["license_type"] is None
        assert "not_collected" not in result

    def test_not_found_under_gap_reports_not_collected(self, collector):
        result = collector._analyze_license({"found": False, "not_collected": True})
        assert result["not_collected"] is True
        assert "collection gap" in result["description"]

    def test_github_could_not_classify_skips_content_matching(self, collector):
        info = {"found": True, "license_key": "other", "spdx_id": "NOASSERTION",
                "license_name": None, "content": "This is a 3-Clause BSD-like custom license"}
        result = collector._analyze_license(info)
        assert result["category"] == "Unknown"

    def test_content_detection_used_when_github_found_nothing(self, collector):
        info = {"found": True, "license_key": None, "spdx_id": None,
                "license_name": None, "content": "MIT License\n\nPermission is..."}
        result = collector._analyze_license(info)
        assert result["spdx_id"] == "MIT"


class TestComplianceScoreGapHandling:
    def test_confirmed_no_license_scores_zero_of_three(self, collector):
        info = {"found": False}
        analysis = collector._analyze_license(info)
        s = collector._calculate_compliance_score(info, analysis)
        assert s["max_score"] == 3
        assert s["score"] == 0

    def test_gapped_no_license_reports_not_collected_status(self, collector):
        info = {"found": False, "not_collected": True}
        analysis = collector._analyze_license(info)
        s = collector._calculate_compliance_score(info, analysis)
        assert s["score"] is None
        assert s["max_score"] == 0
        assert s["status"] == "not_collected"

    def test_found_license_is_never_marked_not_collected(self, collector):
        info = {"found": True, "source": "github_api", "spdx_id": "MIT",
                "license_key": "mit", "license_name": "MIT License"}
        analysis = collector._analyze_license(info)
        s = collector._calculate_compliance_score(info, analysis)
        assert s["max_score"] == 3
        assert s["score"] == 3


class TestGetLicenseFromApiGapHandling:
    def _run(self, collector, forge, license_result):
        forge.license_result = license_result
        return asyncio.run(collector._get_license_from_api(None, "o/r"))

    def test_gap_is_tracked(self, collector, forge):
        result = self._run(collector, forge, COLLECTION_GAP)
        assert result["found"] is False
        assert result["not_collected"] is True

    def test_confirmed_no_license_is_not_a_gap(self, collector, forge):
        result = self._run(collector, forge, None)
        assert result["found"] is False
        assert "not_collected" not in result

    def test_found_license_parses_metadata(self, collector, forge):
        data = {"file_path": "LICENSE", "html_url": "http://x", "size": 100,
                "download_url": "http://raw", "key": "mit", "name": "MIT License",
                "spdx_id": "MIT"}
        result = self._run(collector, forge, data)
        assert result["found"] is True
        assert result["spdx_id"] == "MIT"


class TestCheckLicenseFileGapHandling:
    def _run(self, collector, forge, responses):
        forge.file_metadata_results = responses
        return asyncio.run(collector._check_license_file(None, "o/r"))

    def test_gap_with_no_find_is_not_collected(self, collector, forge):
        responses = {p: COLLECTION_GAP for p in collector.LICENSE_PATTERNS}
        result = self._run(collector, forge, responses)
        assert result["found"] is False
        assert result["not_collected"] is True

    def test_confirmed_absence_on_all_patterns_is_a_real_negative(self, collector, forge):
        responses = {p: None for p in collector.LICENSE_PATTERNS}
        result = self._run(collector, forge, responses)
        assert result["found"] is False
        assert "not_collected" not in result

    def test_found_file_survives_gaps_on_other_patterns(self, collector, forge):
        responses = {p: COLLECTION_GAP for p in collector.LICENSE_PATTERNS}
        responses["LICENSE"] = {"html_url": "http://x", "size": 10, "download_url": ""}
        result = self._run(collector, forge, responses)
        assert result["found"] is True
        assert result["file_path"] == "LICENSE"


class TestCollectGapHandling:
    def test_api_gap_and_manual_gap_reports_not_collected(self, collector):
        async def fake_api(client, ref):
            return {"found": False, "source": "github_api", "not_collected": True}

        async def fake_manual(client, ref):
            return {"found": False, "source": "manual_check", "not_collected": True}

        async def go():
            with patch.object(collector, "_get_license_from_api", side_effect=fake_api), \
                 patch.object(collector, "_check_license_file", side_effect=fake_manual):
                return await collector.collect({"name": "x", "repo_url": "https://github.com/o/r"})

        result = asyncio.run(go())
        assert result["license_info"]["not_collected"] is True
        assert result["compliance_score"]["status"] == "not_collected"

    def test_api_gap_stays_not_collected_even_if_manual_scan_confirms_absence(self, collector):
        # The License API's classifier can recognize a license the manual
        # filename-pattern list doesn't cover, so a gap on it isn't offset
        # by the manual scan coming back clean -- that scan only rules out
        # the patterns it knows about, not "no license anywhere".
        async def fake_api(client, ref):
            return {"found": False, "source": "github_api", "not_collected": True}

        async def fake_manual(client, ref):
            return {"found": False, "source": "manual_check"}

        async def go():
            with patch.object(collector, "_get_license_from_api", side_effect=fake_api), \
                 patch.object(collector, "_check_license_file", side_effect=fake_manual):
                return await collector.collect({"name": "x", "repo_url": "https://github.com/o/r"})

        result = asyncio.run(go())
        assert result["license_info"]["not_collected"] is True

    def test_both_confirm_absence_is_a_real_negative(self, collector):
        async def fake_api(client, ref):
            return {"found": False, "source": "github_api"}

        async def fake_manual(client, ref):
            return {"found": False, "source": "manual_check"}

        async def go():
            with patch.object(collector, "_get_license_from_api", side_effect=fake_api), \
                 patch.object(collector, "_check_license_file", side_effect=fake_manual):
                return await collector.collect({"name": "x", "repo_url": "https://github.com/o/r"})

        result = asyncio.run(go())
        assert "not_collected" not in result["license_info"]
        assert result["compliance_score"]["max_score"] == 3

    def test_api_found_short_circuits_manual_check(self, collector):
        async def fake_api(client, ref):
            return {"found": True, "source": "github_api", "spdx_id": "MIT",
                    "license_key": "mit", "license_name": "MIT License"}

        manual_check = AsyncMock()

        async def go():
            with patch.object(collector, "_get_license_from_api", side_effect=fake_api), \
                 patch.object(collector, "_check_license_file", manual_check):
                return await collector.collect({"name": "x", "repo_url": "https://github.com/o/r"})

        result = asyncio.run(go())
        manual_check.assert_not_called()
        assert result["license_info"]["found"] is True
