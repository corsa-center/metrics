"""Unit tests for MaintainabilityCollector (CASS Section 4.3.6)."""

import pytest

from collectors.quality.maintainability import (
    MaintainabilityCollector, _REFACTOR_INTENT, _LARGE_FILE_BYTES,
)


@pytest.fixture
def collector():
    return MaintainabilityCollector()


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
        c = collector._analyze_tree([
            _f("a/b/c/d.c", size=10), _f("e.c", size=_LARGE_FILE_BYTES + 1),
        ])
        assert c["max_depth"] == 4
        assert c["large_file_share"] == 0.5
        assert c["largest_source_bytes"] == _LARGE_FILE_BYTES + 1

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
