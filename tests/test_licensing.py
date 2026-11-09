"""Unit tests for LicensingCollector."""

import asyncio
import pytest
from formatters.dashboard import DashboardFormatter
from unittest.mock import AsyncMock, patch

from forge.base import COLLECTION_GAP
from collectors.ecosystem.licensing import LicensingCollector


class FakeForge:
    """Minimal stand-in for GitHubForge/GitLabForge."""

    display_name = "GitHub"

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


class TestRowEvidence:
    """What the report shows under the license rows (#99)."""

    API_MIT = {"found": True, "source": "github_api", "file_path": "LICENSE",
               "url": "https://x/LICENSE", "download_url": "https://raw/LICENSE",
               "spdx_id": "MIT", "license_key": "mit", "license_name": "MIT License"}

    def _evidence(self, collector, info, api=None):
        api = info if api is None else api
        rows = collector._row_evidence("o/r", api, info, collector._analyze_license(info))
        return {row["label"]: row["evidence"] for row in rows.values()}

    def test_identified_license_names_its_file_and_identifier(self, collector):
        ev = self._evidence(collector, self.API_MIT)
        assert ev["Enhanced License Detection"]["found"] == [{
            "text": "MIT license (identified by GitHub's license detection)",
            "path": "LICENSE", "url": "https://x/LICENSE"}]
        assert ev["OSI License Validation"] == {"missing": [], "searched": []}

    def test_unnamed_license_says_what_detection_reported(self, collector):
        info = {**self.API_MIT, "spdx_id": "NOASSERTION", "license_key": "other",
                "license_name": "Other"}
        ev = self._evidence(collector, info)
        assert ev["Enhanced License Detection"]["found"][0]["text"] == (
            "License file (GitHub's license detection reported NOASSERTION)")
        assert ev["OSI License Validation"]["missing"] == [{
            "text": "an SPDX license id to check against the OSI-approved list "
                    "(GitHub's license detection reported NOASSERTION)",
            # Dropped when the row passes on the license text instead.
            "unless_met": True}]

    def test_project_level_detection_cites_no_file(self, collector):
        # GitLab names a license without a file it served (no download URL).
        collector.forge.display_name = "GitLab"
        info = {**self.API_MIT, "spdx_id": None, "download_url": ""}
        found = self._evidence(collector, info)["Enhanced License Detection"]["found"]
        assert found == [{"text": "A license (GitLab's license detection reported MIT License)"}]

    def test_unrecognized_spdx_id_lists_the_recognized_ones(self, collector):
        info = {**self.API_MIT, "spdx_id": "EPL-2.0", "license_name": "Eclipse Public License 2.0"}
        osi = self._evidence(collector, info)["OSI License Validation"]
        assert osi["missing"] == [{"text": "EPL-2.0 among the OSI-approved licenses "
                                           "this check recognizes"}]
        assert {"path": "BSD-3-Clause"} in osi["searched"][0]["paths"]

    def test_text_detection_keeps_the_phrases_that_decided_it(self, collector):
        info = {"found": True, "source": "manual_check", "file_path": "COPYING",
                "url": "https://x/COPYING", "spdx_id": None, "license_key": None,
                "license_name": None, "content": "GNU General Public License\nVersion 3, 29 June 2007"}
        ev = self._evidence(collector, info, api={"found": False})
        assert ev["Enhanced License Detection"]["found"] == [{
            "text": "GPL-3.0 (text matched “gnu general public license”, “version 3”)",
            "path": "COPYING", "url": "https://x/COPYING"}]

    def test_unrecognized_file_says_what_was_looked_for(self, collector):
        info = {"found": True, "source": "manual_check", "file_path": "COPYING",
                "url": "https://x/COPYING", "content": "All rights reserved."}
        detection = self._evidence(collector, info, api={"found": False})["Enhanced License Detection"]
        assert detection["found"] == [{"text": "License file", "path": "COPYING",
                                       "url": "https://x/COPYING"}]
        assert [m["text"] for m in detection["missing"]] == [
            "a license GitHub's license detection could name (it detected none)",
            "a license this check recognizes in the text of COPYING (MIT, Apache-2.0, "
            "GPL-2.0, GPL-3.0, BSD-2-Clause, BSD-3-Clause)",
        ]

    def test_gapped_api_and_unread_text_claim_no_search(self, collector):
        info = {"found": True, "source": "manual_check", "file_path": "COPYING",
                "url": "https://x/COPYING", "content": ""}
        ev = self._evidence(collector, info, api={"found": False, "not_collected": True})
        assert ev["Enhanced License Detection"]["missing"] == []
        assert ev["OSI License Validation"]["missing"] == []

    def test_no_license_lists_the_file_names_tried(self, collector):
        detection = self._evidence(collector, {"found": False})["Enhanced License Detection"]
        assert detection["missing"] == [{"text": "License file (GitHub's license detection found none)"}]
        assert detection["searched"][0]["text"] == "License file names tried in o/r"
        assert {"path": "COPYING"} in detection["searched"][0]["paths"]

    def test_gapped_lookup_claims_nothing(self, collector):
        ev = self._evidence(collector, {"found": False, "not_collected": True})
        assert ev["Enhanced License Detection"] == {"found": [], "missing": [], "searched": []}

    def test_collect_returns_the_rows(self, collector, forge):
        forge.license_result = {"file_path": "LICENSE", "html_url": "https://x/LICENSE", "size": 1,
                                "download_url": "https://raw/LICENSE", "key": "mit",
                                "name": "MIT License", "spdx_id": "MIT"}
        result = asyncio.run(collector.collect({"name": "x", "repo_url": "https://github.com/o/r"}))
        labels = [row["label"] for row in result["row_evidence"].values()]
        assert labels == ["Enhanced License Detection", "OSI License Validation"]


class TestReportEvidence:
    def test_evidence_reaches_the_four_two_two_rows(self):
        from orchestrator import MetricsOrchestrator
        from package_report import render_package_report
        from tests.fakes import FakeForge as RepoForge

        forge = RepoForge(license_data={
            "file_path": "LICENSE", "html_url": "https://github.com/o/r/blob/HEAD/LICENSE",
            "size": 1, "download_url": "https://raw/LICENSE", "spdx_id": "EPL-2.0",
            "key": "epl-2.0", "name": "Eclipse Public License 2.0", "text": "EPL"})
        licensing = asyncio.run(LicensingCollector(forge).collect(
            {"name": "x", "repo_url": "https://github.com/o/r"}))
        metrics = {
            "overall_score": 27, "last_updated": "2026-09-30T02:25:04+00:00",
            "dimensions": {
                "impact": {"score": 0.0, "metadata": {"status": "placeholder"}},
                "ecosystem": {"score": 80.0, "sub_results": {"licensing": licensing}},
                "quality": {"score": 0.0, "sub_results": {}},
            },
        }
        orchestrator = MetricsOrchestrator(config_path="config/orchestrator.yaml")
        dashboard = DashboardFormatter().format(metrics, {})
        page = render_package_report("o/r", dashboard, metrics, orchestrator._metric_weights())

        def row(label):
            text = page[page.index(f"<b>{label}:</b>"):]
            return text[:text.index("</li>")]

        assert ("<b>Found:</b> EPL-2.0 license (identified by GitHub&#x27;s license detection) in "
                '<a href="https://github.com/o/r/blob/HEAD/LICENSE">LICENSE</a>'
                ) in row("Enhanced License Detection")
        osi = row("OSI License Validation")
        assert "<b>Not found:</b> EPL-2.0 among the OSI-approved licenses" in osi
        assert "<b>OSI-approved licenses recognized:</b> MIT, Apache-2.0" in osi
