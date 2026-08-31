"""Unit tests for ReliabilityCollector (CASS Section 4.3.1)."""

import pytest

from collectors.quality.reliability import (
    ReliabilityCollector, _ANALYSIS_WORKFLOW_HINT, _HARDENING_MARKERS,
    _FLAG_FILE_HINT, _DEFECT_LABELS,
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
