"""Unit tests for ReliabilityCollector (CASS Section 4.3.1)."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from collectors.ecosystem.base import COLLECTION_GAP, RepoTree
from collectors.quality.reliability import (
    ReliabilityCollector, _ANALYSIS_WORKFLOW_HINT, _HARDENING_MARKERS,
    _FLAG_FILE_HINT, _DEFECT_LABELS, _ANALYSIS_CONFIGS,
)


@pytest.fixture
def collector():
    return ReliabilityCollector()


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


class TestFindAnalysisTools:
    """_find_analysis_tools now takes a RepoTree (or COLLECTION_GAP) directly
    -- see METRIC_BLIND_SPOTS.md class F1.
    """

    def test_gapped_tree_with_no_ci_matches_reports_gap(self, collector):
        tools, saw_gap = asyncio.run(collector._find_analysis_tools(COLLECTION_GAP, []))
        assert tools == []
        assert saw_gap is True

    def test_config_file_found(self, collector):
        tree = RepoTree("o", "r", [".clang-tidy"], truncated=False)
        tools, saw_gap = asyncio.run(collector._find_analysis_tools(tree, []))
        assert tools == ["clang-tidy"]
        assert saw_gap is False

    def test_confirmed_absence_is_not_a_gap(self, collector):
        tree = RepoTree("o", "r", ["README.md"], truncated=False)
        tools, saw_gap = asyncio.run(collector._find_analysis_tools(tree, []))
        assert tools == []
        assert saw_gap is False

    def test_ci_text_match_does_not_need_a_config_file(self, collector):
        tools, saw_gap = asyncio.run(
            collector._find_analysis_tools(COLLECTION_GAP, ["run: cppcheck ."])
        )
        assert "Cppcheck" in tools


class TestFindFlagFiles:
    """_find_flag_files now searches the whole tree by filename regex,
    rather than listing four fixed directories -- AMReX's actual flag file
    (Tools/CMake/AMReXFlagsTargets.cmake) sits outside all four
    (corsa-center/metrics#51, METRIC_BLIND_SPOTS.md class F3).
    """

    def test_gapped_tree_is_tracked(self, collector):
        paths, saw_gap = collector._find_flag_files(COLLECTION_GAP)
        assert paths == []
        assert saw_gap is True

    def test_confirmed_no_flag_files_is_not_a_gap(self, collector):
        tree = RepoTree("o", "r", ["README.md", "CMakeLists.txt"], truncated=False)
        paths, saw_gap = collector._find_flag_files(tree)
        assert paths == []
        assert saw_gap is False

    def test_flag_file_found_outside_conventional_directories(self, collector):
        tree = RepoTree("o", "r", ["Tools/CMake/AMReXFlagsTargets.cmake"], truncated=False)
        paths, saw_gap = collector._find_flag_files(tree)
        assert paths == ["Tools/CMake/AMReXFlagsTargets.cmake"]
        assert saw_gap is False

    def test_result_capped_at_max_flag_files(self, collector):
        tree = RepoTree(
            "o", "r",
            [f"cmake/warn{i}.cmake" for i in range(10)],
            truncated=False,
        )
        paths, _ = collector._find_flag_files(tree)
        assert len(paths) == 4

    def test_non_cmake_files_do_not_crowd_out_the_cap(self, collector):
        # SECURITY.md ("secur") and a CI workflow named flag_prs_to_master.yml
        # ("flag") both match _FLAG_FILE_HINT by keyword alone -- restricting
        # to .cmake keeps them from spending the small result cap on
        # Trilinos-shaped false positives.
        tree = RepoTree(
            "o", "r",
            ["SECURITY.md", ".github/workflows/flag_prs_to_master.yml",
             "cmake/tribits/WarningFlags.cmake"],
            truncated=False,
        )
        paths, _ = collector._find_flag_files(tree)
        assert paths == ["cmake/tribits/WarningFlags.cmake"]


class TestDefectTrendGapHandling:
    def _run(self, collector, search_side_effect):
        async def go():
            client = MagicMock()
            with patch("collectors.quality.reliability.search_get", side_effect=search_side_effect):
                return await collector._defect_trend(client, "o", "r")

        return asyncio.run(go())

    def test_search_failure_is_not_collected_not_a_confirmed_zero(self, collector):
        result = self._run(collector, AsyncMock(return_value=None))
        assert result["not_collected"] is True
        assert result["measurable"] is False

    def test_confirmed_low_volume_is_a_real_unmeasurable_not_a_gap(self, collector):
        resp = MagicMock()
        resp.json.return_value = {"total_count": 0}
        result = self._run(collector, AsyncMock(return_value=resp))
        assert result["measurable"] is False
        assert "not_collected" not in result
