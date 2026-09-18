"""Unit tests for ReliabilityCollector (CASS Section 4.3.1)."""

import asyncio
import pytest

from forge.base import COLLECTION_GAP
from collectors.quality.reliability import (
    ReliabilityCollector, _ANALYSIS_WORKFLOW_HINT, _HARDENING_MARKERS,
    _FLAG_FILE_HINT, _DEFECT_LABELS, _ANALYSIS_CONFIGS, _FLAG_DIRECTORIES,
)


class FakeForge:
    """Minimal stand-in for GitHubForge/GitLabForge."""

    def __init__(self):
        self.file_results = {}
        self.dir_listing_results = {}
        self.search_result = 0

    def extract_ref(self, repo_url):
        return None if repo_url == "not-a-url" else "o/r"

    async def file_exists(self, client, ref, path):
        return self.file_results.get(path)

    async def dir_listing(self, client, ref, path):
        return self.dir_listing_results.get(path, [])

    async def search_issues(self, client, query, *, per_page=1):
        return self.search_result

    def get_timestamp(self):
        return "2026-01-01T00:00:00+00:00"


@pytest.fixture
def forge():
    return FakeForge()


@pytest.fixture
def collector(forge):
    return ReliabilityCollector(forge)


class TestWorkflowSelection:
    @pytest.mark.parametrize("name", [
        "analysis.yml", "codeql.yml", "sanitizers.yml", "clang-tidy.yml",
        "sonar.yml", "nightly-asan.yml", "security-scan.yml",
    ])
    def test_analysis_workflows_selected(self, name):
        assert _ANALYSIS_WORKFLOW_HINT.search(name)

    @pytest.mark.parametrize("name", [
        "linkchecker.yml", "markdown-link-check.yml", "review-checklist.yml",
    ])
    def test_generic_check_workflows_not_selected(self, name):
        # "check" used to match these, and they consumed the read budget
        # before any workflow that actually builds the code.
        assert not _ANALYSIS_WORKFLOW_HINT.search(name)


class TestFlagFileDiscovery:
    @pytest.mark.parametrize("name", [
        "sanitizers.cmake", "CompilerWarnings.cmake", "HardeningFlags.cmake",
        "HDFCompilerFlags.cmake", "security.cmake",
    ])
    def test_flag_files_matched(self, name):
        assert _FLAG_FILE_HINT.search(name)

    def test_unrelated_cmake_not_matched(self):
        assert not _FLAG_FILE_HINT.search("FindZLIB.cmake")


class TestHardeningMarkers:
    @pytest.mark.parametrize("text,label", [
        ("set(CMAKE_C_FLAGS -Werror)", "Warnings as errors"),
        ("-D_FORTIFY_SOURCE=2", "Fortify source"),
        ("-fstack-protector-strong", "Stack protector"),
        ("-fsanitize=address", "Sanitizers"),
        ("Follows CERT-C guidance", "CERT / MISRA reference"),
        ("MISRA compliance checked", "CERT / MISRA reference"),
    ])
    def test_markers(self, text, label):
        assert _HARDENING_MARKERS[label].search(text)


class TestDefectTrendScoring:
    def _score(self, collector, trend):
        return collector._calculate_score([], [], trend)["sub_scores"]["reliability_trend"]

    def test_unmeasurable_is_reported_not_faked(self, collector):
        # Reporting "0 vs 0, stable" for a project that records no defects
        # would be a fabricated pass.
        info = self._score(collector, {"measurable": False, "recent": 0, "previous": 0})
        assert not info["passing"]
        assert "does not record defect reports" in info["value"]

    def test_stable_passes(self, collector):
        info = self._score(collector, {"measurable": True, "recent": 10,
                                       "previous": 10, "direction": "stable",
                                       "source": "issue type"})
        assert info["passing"]
        assert "by issue type" in info["value"]

    def test_improving_passes(self, collector):
        assert self._score(collector, {"measurable": True, "recent": 4,
                                       "previous": 20, "direction": "improving",
                                       "source": "label"})["passing"]

    def test_increasing_fails(self, collector):
        assert not self._score(collector, {"measurable": True, "recent": 149,
                                           "previous": 103, "direction": "increasing",
                                           "source": "issue type"})["passing"]


class TestLabelQuoting:
    def test_labels_with_spaces_are_quoted(self):
        # Unquoted, a label containing a space splits the search query and
        # silently drops the rest of the list — every project returned 0.
        quoted = ",".join(
            f'"{l}"' if (" " in l or ":" in l) else l for l in _DEFECT_LABELS
        )
        assert '"type: bug"' in quoted
        assert '"bug report"' in quoted
        assert quoted.startswith("bug,defect")


class TestScoring:
    def test_thresholds(self, collector):
        s = collector._calculate_score(
            ["clang-tidy"], ["Sanitizers"],
            {"measurable": True, "recent": 5, "previous": 5,
             "direction": "stable", "source": "label"},
        )
        assert s["score"] == 3 and s["max_score"] == 3

    def test_no_signals(self, collector):
        assert collector._calculate_score([], [], {"measurable": False})["score"] == 0

    def test_single_hardening_indicator_passes(self, collector):
        # Calibrated against the portfolio: HDF5 shows one, others none.
        s = collector._calculate_score([], ["Sanitizers"], {"measurable": False})
        assert s["sub_scores"]["cert_compliance"]["passing"]

    def test_cert_row_states_it_is_not_conformance(self, collector):
        s = collector._calculate_score([], ["Sanitizers"], {"measurable": False})
        assert "not audited conformance" in s["sub_scores"]["cert_compliance"]["detail"]


class TestScoringGapHandling:
    def test_empty_tools_under_gap_is_not_collected_not_a_negative(self, collector):
        s = collector._calculate_score([], [], {"measurable": False}, tools_gap=True)
        entry = s["sub_scores"]["advanced_static_analysis"]
        assert entry["passing"] is False
        assert entry["not_collected"] is True

    def test_found_tool_survives_a_gap(self, collector):
        s = collector._calculate_score(["clang-tidy"], [], {"measurable": False}, tools_gap=True)
        entry = s["sub_scores"]["advanced_static_analysis"]
        assert entry["passing"] is True
        assert "not_collected" not in entry

    def test_empty_hardening_under_gap_is_not_collected(self, collector):
        s = collector._calculate_score([], [], {"measurable": False}, hardening_gap=True)
        entry = s["sub_scores"]["cert_compliance"]
        assert entry["not_collected"] is True

    def test_trend_not_collected_is_excluded_not_scored_as_failure(self, collector):
        trend = {"measurable": False, "recent": 0, "previous": 0,
                  "direction": None, "not_collected": True}
        s = collector._calculate_score(["clang-tidy"], ["Sanitizers"], trend)
        # If not_collected silently counted as a failure this would be 2/3.
        assert s["score"] == 2
        assert s["max_score"] == 2
        assert s["percentage"] == 100.0

    def test_everything_gapped_reports_not_collected_status(self, collector):
        trend = {"measurable": False, "recent": 0, "previous": 0,
                  "direction": None, "not_collected": True}
        s = collector._calculate_score([], [], trend, tools_gap=True, hardening_gap=True)
        assert s["score"] is None
        assert s["max_score"] == 0
        assert s["status"] == "not_collected"


class TestFindAnalysisToolsGapHandling:
    def _run(self, collector, forge, responses, workflows=None):
        forge.file_results = responses
        return asyncio.run(collector._find_analysis_tools(None, "o/r", workflows or []))

    def test_gapped_config_check_with_no_finds_reports_gap(self, collector, forge):
        responses = {p: COLLECTION_GAP for paths in _ANALYSIS_CONFIGS.values() for p in paths}
        tools, saw_gap = self._run(collector, forge, responses)
        assert tools == []
        assert saw_gap is True

    def test_found_tool_survives_gaps_on_others(self, collector, forge):
        responses = {p: COLLECTION_GAP for paths in _ANALYSIS_CONFIGS.values() for p in paths}
        responses[".clang-tidy"] = "http://x"
        tools, saw_gap = self._run(collector, forge, responses)
        assert tools == ["clang-tidy"]

    def test_ci_text_match_does_not_need_file_probe(self, collector, forge):
        responses = {p: COLLECTION_GAP for paths in _ANALYSIS_CONFIGS.values() for p in paths}
        tools, saw_gap = self._run(collector, forge, responses, workflows=["run: cppcheck ."])
        assert "Cppcheck" in tools


class TestFindFlagFilesGapHandling:
    def _run(self, collector, forge, responses):
        forge.dir_listing_results = {
            d: (COLLECTION_GAP if responses.get(d) is COLLECTION_GAP else [])
            for d in _FLAG_DIRECTORIES
        }
        return asyncio.run(collector._find_flag_files(None, "o/r"))

    def test_gapped_directory_listing_is_tracked(self, collector, forge):
        responses = {_FLAG_DIRECTORIES[0]: COLLECTION_GAP}
        paths, saw_gap = self._run(collector, forge, responses)
        assert saw_gap is True

    def test_confirmed_missing_directory_is_not_a_gap(self, collector, forge):
        responses = {d: None for d in _FLAG_DIRECTORIES}
        paths, saw_gap = self._run(collector, forge, responses)
        assert paths == []
        assert saw_gap is False


class TestDefectTrendGapHandling:
    def _run(self, collector, forge, search_result):
        forge.search_result = search_result
        return asyncio.run(collector._defect_trend(None, "o/r"))

    def test_search_failure_is_not_collected_not_a_confirmed_zero(self, collector, forge):
        result = self._run(collector, forge, None)
        assert result["not_collected"] is True
        assert result["measurable"] is False

    def test_confirmed_low_volume_is_a_real_unmeasurable_not_a_gap(self, collector, forge):
        result = self._run(collector, forge, 0)
        assert result["measurable"] is False
        assert "not_collected" not in result
