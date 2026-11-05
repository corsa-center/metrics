"""Unit tests for MaintainabilityCollector (CASS Section 4.3.6)."""

import asyncio

import pytest

from collectors.ecosystem.base import get_threshold
from collectors.quality.maintainability import MaintainabilityCollector, _REFACTOR_INTENT
from orchestrator import MetricsOrchestrator
from package_report import render_package_report
from tests.fakes import FakeForge


@pytest.fixture
def collector():
    # None: every test here exercises pure computation methods
    # (_analyze_tree/_calculate_score) that never touch self.forge.
    return MaintainabilityCollector(None)


def _f(path, size=1000):
    return {"path": path, "size": size}


class TestTreeClassification:
    def test_test_files_are_not_counted_as_source(self, collector):
        c = collector._analyze_tree([_f("src/a.c"), _f("test/b.c"), _f("tests/c.c")])
        assert c["source_files"] == 1
        assert c["test_files"] == 2

    def test_test_filename_convention(self, collector):
        c = collector._analyze_tree([_f("src/test_thing.py"), _f("src/thing_test.py")])
        assert c["test_files"] == 2
        assert c["source_files"] == 0

    def test_images_under_docs_are_not_documentation(self, collector):
        # HDF5's docs tree holds 165 .gif and 82 .png; counting them doubled
        # its documentation figure.
        c = collector._analyze_tree([
            _f("docs/a.gif"), _f("docs/b.png"), _f("docs/guide.md"),
        ])
        assert c["doc_files"] == 1

    def test_source_under_docs_is_not_documentation(self, collector):
        c = collector._analyze_tree([_f("docs/example.c")])
        assert c["doc_files"] == 0
        assert c["source_files"] == 1

    def test_txt_counts_only_at_root_or_under_docs(self, collector):
        c = collector._analyze_tree([
            _f("INSTALL.txt"),           # root -> documentation
            _f("doc/notes.txt"),         # doc tree -> documentation
            _f("src/fixtures/ref.txt"),  # buried -> not documentation
        ])
        assert c["doc_files"] == 2

    def test_test_fixtures_are_never_documentation(self, collector):
        c = collector._analyze_tree([_f("test/expected/out.txt"), _f("tests/readme.md")])
        assert c["doc_files"] == 0

    def test_doc_generator_detection(self, collector):
        assert collector._analyze_tree([_f("Doxyfile")])["doc_generators"] == ["Doxygen"]
        assert collector._analyze_tree([_f("docs/conf.py")])["doc_generators"] == ["Sphinx"]
        assert collector._analyze_tree([_f("mkdocs.yml")])["doc_generators"] == ["MkDocs"]

    def test_depth_and_size_stats(self, collector):
        large_file_bytes = get_threshold("4.3.6", "Advanced Complexity Analysis", "large_file_bytes")
        c = collector._analyze_tree([
            _f("a/b/c/d.c", size=10), _f("e.c", size=large_file_bytes + 1),
        ])
        assert c["max_depth"] == 4
        assert c["large_file_share"] == 0.5
        assert c["largest_source_bytes"] == large_file_bytes + 1

    def test_empty_tree(self, collector):
        c = collector._analyze_tree([])
        assert c["source_files"] == 0
        assert c["large_file_share"] == 0.0


class TestRefactorPattern:
    @pytest.mark.parametrize("subject", [
        "Refactor the H5E module",
        "cmake remove noop policies (#6586)",
        "CMake Fortran float128 check simplification",
        "Deprecate the old API",
        "clean up dead code",
        "remove unused helper",
    ])
    def test_matches_structural_work(self, subject):
        assert _REFACTOR_INTENT.search(subject)

    @pytest.mark.parametrize("subject", [
        "Remove abort on infinite loop during library close (#6532)",
        "Fix segfault in example: swap inverted mem_space_id",
        "Fix CVE-2026-19025",
        "Add per-area review checklist",
    ])
    def test_does_not_match_bug_fixes(self, subject):
        # A bare "remove" matched bug fixes and inflated the refactoring rate.
        assert not _REFACTOR_INTENT.search(subject)


class TestScoring:
    def _comp(self, **kw):
        base = {"source_files": 100, "test_files": 30, "doc_files": 10,
                "max_depth": 5, "mean_source_bytes": 5000,
                "largest_source_bytes": 9000, "large_file_share": 0.01,
                "doc_generators": [], "truncated": False, "languages": []}
        base.update(kw)
        return base

    def _ref(self, share=0.05, sampled=300):
        return {"sampled": sampled, "refactor_commits": int(share * sampled),
                "share": share, "examples": []}

    def test_all_passing(self, collector):
        s = collector._calculate_score(self._comp(), self._ref())
        assert s["score"] == 4

    def test_oversized_files_fail_complexity(self, collector):
        s = collector._calculate_score(self._comp(large_file_share=0.06), self._ref())
        assert not s["sub_scores"]["complexity_analysis"]["passing"]

    def test_deep_nesting_fails_complexity(self, collector):
        s = collector._calculate_score(self._comp(max_depth=11), self._ref())
        assert not s["sub_scores"]["complexity_analysis"]["passing"]

    def test_test_ratio_threshold(self, collector):
        assert collector._calculate_score(self._comp(test_files=20), self._ref())[
            "sub_scores"]["code_quality"]["passing"]
        assert not collector._calculate_score(self._comp(test_files=19), self._ref())[
            "sub_scores"]["code_quality"]["passing"]

    def test_doc_generator_passes_even_with_few_doc_files(self, collector):
        s = collector._calculate_score(
            self._comp(doc_files=1, doc_generators=["Doxygen"]), self._ref())
        assert s["sub_scores"]["documentation_quality"]["passing"]

    def test_refactor_threshold(self, collector):
        assert collector._calculate_score(self._comp(), self._ref(share=0.02))[
            "sub_scores"]["refactoring_tracking"]["passing"]
        assert not collector._calculate_score(self._comp(), self._ref(share=0.01))[
            "sub_scores"]["refactoring_tracking"]["passing"]

    def test_no_commits_sampled(self, collector):
        s = collector._calculate_score(
            self._comp(), {"sampled": 0, "refactor_commits": 0, "share": None})
        assert not s["sub_scores"]["refactoring_tracking"]["passing"]

    def test_truncated_tree_is_flagged_in_the_value(self, collector):
        s = collector._calculate_score(self._comp(truncated=True), self._ref())
        assert "approximate" in s["sub_scores"]["complexity_analysis"]["value"]

    def test_no_source_files(self, collector):
        s = collector._calculate_score(self._comp(source_files=0), self._ref())
        assert not s["sub_scores"]["complexity_analysis"]["passing"]
        assert not s["sub_scores"]["code_quality"]["passing"]

    def test_max_score_is_four(self, collector):
        assert collector._calculate_score(self._comp(), self._ref())["max_score"] == 4


class TestTestNamingConventions:
    @pytest.mark.parametrize("path", [
        "core/unit_test/TestViewAPI.hpp",
        "algorithms/unit_tests/TestSort.cpp",
        "packages/tpetra/core/src/Tpetra_Map_UnitTests.cpp",
        "hello_world/hello_world_unit_tests.cpp",
        "src/thing_tests.py",
    ])
    def test_counted_as_test(self, collector, path):
        c = collector._analyze_tree([_f(path), _f("src/a.c")])
        assert c["test_files"] == 1

    @pytest.mark.parametrize("path", ["src/latest.py", "src/contest.c", "src/testament.c"])
    def test_not_counted_as_test(self, collector, path):
        assert collector._analyze_tree([_f(path)])["test_files"] == 0


class TestDepthIgnoresTestData:
    def test_deep_fixture_does_not_set_depth(self, collector):
        c = collector._analyze_tree([
            _f("lib/spack/spack/test/data/mirrors/v2/build_cache/a/b/c/d/e.py"),
            _f("lib/spack/spack/cmd/install.py"),
        ])
        assert c["max_depth"] == 5

    def test_deep_code_still_counts(self, collector):
        c = collector._analyze_tree([_f("a/b/c/d/e/f/g/h/i/j/k/deep.cpp")])
        assert c["max_depth"] == 12


def _collect(**forge_kwargs):
    forge = FakeForge(**forge_kwargs)
    return asyncio.run(MaintainabilityCollector(forge).collect(
        {"name": "R", "repo_url": "https://github.com/o/r"}))


def _subs(result):
    return result["overall_score"]["sub_scores"]


class TestEvidence:
    """What report.html shows under each 4.3.6 row (issue #99)."""

    def test_oversized_and_deepest_source_files_are_named(self):
        large = get_threshold("4.3.6", "Advanced Complexity Analysis", "large_file_bytes")
        result = _collect(files={
            "src/big.c": "x" * (large + 1024), "src/small.c": "x",
            "src/a/b/deep.c": "x", "test/deeper/still/t.c": "x",
        })
        found = _subs(result)["complexity_analysis"]["evidence"]["found"]
        assert found[0]["path"] == "src/big.c"
        assert found[0]["text"].endswith(" KB source file")
        assert found[0]["url"] == "https://github.com/o/r/blob/HEAD/src/big.c"
        # Test fixtures don't set the depth, so they aren't named as deepest.
        assert found[-1] == {"text": "deepest source file (depth 4)", "path": "src/a/b/deep.c",
                             "url": "https://github.com/o/r/blob/HEAD/src/a/b/deep.c"}
        assert len(found) == 2

    def test_largest_file_is_named_when_none_is_oversized(self, collector):
        c = collector._analyze_tree([_f("a.c", 2048), _f("b.c", 1024)])
        found = collector._calculate_score(c, {"sampled": 0})["sub_scores"][
            "complexity_analysis"]["evidence"]["found"]
        assert found[0]["text"] == "largest source file (2.0 KB)"
        assert found[0]["path"] == "a.c"

    def test_test_files_are_counted_by_directory(self):
        result = _collect(files={
            "src/a.c": "x", "test/a.c": "x", "test/b.c": "x",
            "src/x/unit_tests/c.cpp": "x", "thing_test.py": "x",
        })
        found = _subs(result)["code_quality"]["evidence"]["found"]
        assert [(f["path"], f["text"]) for f in found] == [
            ("test", "2 test files"), ("the repository root", "1 test file"),
            ("src/x/unit_tests", "1 test file"),
        ]
        assert found[0]["url"] == "https://github.com/o/r/tree/HEAD/test"
        assert found[1]["url"] is None

    def test_no_test_files_says_what_would_count(self, collector):
        c = collector._analyze_tree([_f("src/a.c")])
        evidence = collector._calculate_score(c, {"sampled": 0})["sub_scores"][
            "code_quality"]["evidence"]
        assert evidence["found"] == []
        assert evidence["missing"][0]["text"].startswith("test files: source files under a test")

    def test_doc_generator_config_is_named(self, collector):
        c = collector._analyze_tree([_f("src/a.c"), _f("doxygen/Doxyfile.in"), _f("README.md")])
        evidence = collector._calculate_score(c, {"sampled": 0})["sub_scores"][
            "documentation_quality"]["evidence"]
        assert evidence["found"] == [
            {"text": "Doxygen configuration", "path": "doxygen/Doxyfile.in", "url": None}]
        assert evidence["missing"] == []

    def test_missing_doc_generator_and_docs_say_what_was_looked_for(self, collector):
        c = collector._analyze_tree([_f("src/a.c")])
        missing = collector._calculate_score(c, {"sampled": 0})["sub_scores"][
            "documentation_quality"]["evidence"]["missing"]
        assert "(Doxyfile, docs/conf.py, docs/source/conf.py, mkdocs.yml" in missing[0]["text"]
        assert missing[1]["text"].startswith("documentation files: .adoc, .dox, .md")

    def test_empty_tree_claims_no_search(self, collector):
        # An empty tree is also what a failed tree fetch leaves behind.
        sub = collector._calculate_score(collector._analyze_tree([]), {"sampled": 0})["sub_scores"]
        assert not any("evidence" in s for s in sub.values())

    def test_refactor_words_matched_and_looked_for(self):
        result = _collect(commit_list=[
            {"sha": "1", "subject": "Refactor H5T"}, {"sha": "2", "subject": "refactor H5S"},
            {"sha": "3", "subject": "Clean up tests"}, {"sha": "4", "subject": "Fix a bug"},
        ])
        evidence = _subs(result)["refactoring_tracking"]["evidence"]
        assert [f["text"] for f in evidence["found"]] == [
            "“refactor” in 2 commit subjects", "“clean up” in 1 commit subject"]
        assert evidence["searched"][0]["text"] == (
            "Words looked for in the 4 most recent commit subjects")
        assert {"path": "tidy"} in evidence["searched"][0]["paths"]

    def test_no_commits_read_claims_no_search(self):
        result = _collect(files={"src/a.c": "x"}, gaps={"commits"})
        assert "evidence" not in _subs(result)["refactoring_tracking"]


def _row(page, label):
    """One row of report.html, from its label to the next row."""
    start = page.index(f"<b>{label}:</b>")
    end = page.find("<li>", start)
    return page[start:end if end != -1 else None]


class TestReportEvidence:
    def test_evidence_reaches_the_four_three_six_rows(self):
        result = _collect(
            files={"src/a/b/deep.c": "x", "src/b.c": "x"},
            commit_list=[{"sha": "1", "subject": "Fix a bug"}],
        )
        metrics = {
            "overall_score": 27,
            "last_updated": "2026-09-30T02:25:04.582663+00:00",
            "dimensions": {
                "impact": {"score": 0.0, "metadata": {"status": "placeholder"}},
                # 4.3.6 renders only alongside 4.2.3's bus factor.
                "ecosystem": {"score": 80.0, "sub_results": {"maintenance": {
                    "contributor_activity": {"bus_factor": 3, "top_contributor_pct": 40,
                                             "total_contributors": 9}}}},
                "quality": {"score": 0.0, "sub_results": {"maintainability": result}},
            },
        }
        orchestrator = MetricsOrchestrator(config_path="config/orchestrator.yaml")
        dashboard = orchestrator._transform_for_dashboard(metrics, {})
        page = render_package_report("o/r", dashboard, metrics, orchestrator._metric_weights())

        assert ('<b>Found:</b> deepest source file (depth 4) in <a href="'
                'https://github.com/o/r/blob/HEAD/src/a/b/deep.c">src/a/b/deep.c</a>'
                in _row(page, "Advanced Complexity Analysis"))
        assert ("<b>Not found:</b> test files: source files under"
                in _row(page, "Code Quality Assessment"))
        assert ("<b>Not found:</b> documentation generator configuration (Doxyfile"
                in _row(page, "Documentation Quality Evaluation"))
        assert ("<b>Words looked for in the 1 most recent commit subjects:</b> refactor…"
                in _row(page, "Refactoring and Evolution Tracking"))
