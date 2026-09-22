"""Unit tests for UsabilityCollector (CASS Section 4.3.4)."""

import pytest

from collectors.ecosystem.base import COLLECTION_GAP, RepoTree
from collectors.quality.usability import UsabilityCollector, _README_SECTIONS


@pytest.fixture
def collector():
    return UsabilityCollector()


class TestScoring:
    def _score(self, collector, sections=(), doc_dir=None, site=None):
        readme = {"exists": True, "sections": list(sections),
                  "missing": [s for s in _README_SECTIONS if s not in sections]}
        return collector._calculate_score(readme, doc_dir, site)

    def test_three_sections_is_complete(self, collector):
        s = self._score(collector, sections=["Installation", "Usage", "Examples"])
        assert s["sub_scores"]["documentation_completeness"]["passing"]

    def test_two_sections_alone_is_not(self, collector):
        s = self._score(collector, sections=["Usage", "Support"])
        assert not s["sub_scores"]["documentation_completeness"]["passing"]

    def test_thin_readme_backed_by_real_docs_passes(self, collector):
        # HDF5's shape: a short README, but a docs/ tree and a published site.
        s = self._score(collector, sections=["Usage", "Support"],
                        doc_dir="docs", site={"url": "https://example.org"})
        assert s["sub_scores"]["documentation_completeness"]["passing"]

    def test_no_readme_sections_never_passes(self, collector):
        s = self._score(collector, sections=[], doc_dir="docs",
                        site={"url": "https://example.org"})
        assert not s["sub_scores"]["documentation_completeness"]["passing"]

    def test_value_mentions_each_signal(self, collector):
        s = self._score(collector, sections=["Usage"], doc_dir="doc",
                        site={"url": "https://example.org"})
        value = s["sub_scores"]["documentation_completeness"]["value"]
        assert "1/4 core sections" in value
        assert "doc/ present" in value
        assert "documentation site published" in value

    def test_three_submetrics_uncollected(self, collector):
        sub = self._score(collector)["sub_scores"]
        assert sum(1 for v in sub.values() if v.get("not_collected")) == 3
        # The 3 permanently-uncollected submetrics must not inflate the
        # denominator -- only documentation_completeness is ever scorable.
        assert self._score(collector)["max_score"] == 1

    def test_gapped_negative_is_not_collected_not_a_confirmed_fail(self, collector):
        readme = {"exists": False, "sections": [], "missing": list(_README_SECTIONS),
                   "not_collected": True}
        s = collector._calculate_score(readme, None, None, has_gap=False)
        entry = s["sub_scores"]["documentation_completeness"]
        assert entry["passing"] is False
        assert entry["not_collected"] is True
        assert s["max_score"] == 0
        assert s["status"] == "not_collected"

    def test_doc_dir_or_site_gap_taints_a_negative_result(self, collector):
        readme = {"exists": True, "sections": ["Usage"], "missing": []}
        s = collector._calculate_score(readme, None, None, has_gap=True)
        entry = s["sub_scores"]["documentation_completeness"]
        assert entry["passing"] is False
        assert entry["not_collected"] is True

    def test_positive_result_survives_a_gap(self, collector):
        # 3+ sections alone is enough to pass, regardless of whether the
        # doc-dir/site lookups gapped.
        readme = {"exists": True, "sections": ["Installation", "Usage", "Examples"],
                   "missing": []}
        s = collector._calculate_score(readme, None, None, has_gap=True)
        entry = s["sub_scores"]["documentation_completeness"]
        assert entry["passing"] is True
        assert "not_collected" not in entry


class TestHeadingDetection:
    def _sections(self, collector, markdown):
        import re
        from collectors.quality.usability import _ATX_HEADING, _SETEXT_HEADING
        headings = _ATX_HEADING.findall(markdown) + _SETEXT_HEADING.findall(markdown)
        return [
            label for label, pattern in _README_SECTIONS.items()
            if any(re.search(pattern, h, re.IGNORECASE) for h in headings)
        ]

    def test_atx_headings(self, collector):
        assert "Installation" in self._sections(collector, "# Intro\n## Installation\ntext")

    def test_setext_headings(self, collector):
        assert "Usage" in self._sections(collector, "Usage\n-----\nsome text")

    def test_body_mentions_do_not_count(self, collector):
        # "install" in a paragraph isn't a documented installation section.
        assert self._sections(collector, "# Intro\nYou can install it somehow.") == []

    def test_synonyms_match(self, collector):
        assert "Installation" in self._sections(collector, "## Getting Started")
        assert "Usage" in self._sections(collector, "## Quick Start")


class TestEmptyResult:
    def test_invalid_url(self, collector):
        import asyncio
        r = asyncio.run(collector.collect({"name": "x", "repo_url": "nope"}))
        assert r["readme"]["exists"] is False
        assert r["overall_score"]["score"] == 0


class TestAnalyzeReadmeGapHandling:
    def _run(self, collector, client):
        import asyncio
        return asyncio.run(collector._analyze_readme(client, "o", "r"))

    def test_gap_is_not_collected_not_a_confirmed_missing_readme(self, collector):
        from unittest.mock import AsyncMock, patch
        with patch.object(collector, "_github_get", new=AsyncMock(return_value=COLLECTION_GAP)):
            result = self._run(collector, None)
        assert result["exists"] is False
        assert result["not_collected"] is True

    def test_confirmed_404_is_a_real_negative(self, collector):
        from unittest.mock import AsyncMock, patch
        with patch.object(collector, "_github_get", new=AsyncMock(return_value=None)):
            result = self._run(collector, None)
        assert result["exists"] is False
        assert "not_collected" not in result


class TestFindDocDirectory:
    """_find_doc_directory now takes a RepoTree (or COLLECTION_GAP) directly,
    rather than probing paths one at a time -- see corsa-center/metrics#54 and
    METRIC_BLIND_SPOTS.md class F1.
    """

    def test_gap_tree_is_tracked(self, collector):
        path, saw_gap = collector._find_doc_directory(COLLECTION_GAP)
        assert path is None
        assert saw_gap is True

    def test_found_directory_reports_no_gap(self, collector):
        tree = RepoTree("o", "r", ["docs/index.md"], truncated=False)
        path, saw_gap = collector._find_doc_directory(tree)
        assert path == "docs"
        assert saw_gap is False

    def test_capitalized_docs_directory_is_found(self, collector):
        # AMReX-Codes/amrex ships "Docs" (capital D, lowercase rest); the
        # waiver could never fire for it when case had to be enumerated.
        # Reported in corsa-center/metrics#54.
        tree = RepoTree("o", "r", ["Docs/index.rst"], truncated=False)
        path, saw_gap = collector._find_doc_directory(tree)
        assert path == "docs"
        assert saw_gap is False

    def test_screaming_case_doc_directory_is_found(self, collector):
        # superlu/superlu_dist/superlu_mt ship DOC/, not doc/ or docs/.
        tree = RepoTree("o", "r", ["DOC/html/index.html"], truncated=False)
        path, saw_gap = collector._find_doc_directory(tree)
        assert path == "doc"
        assert saw_gap is False

    def test_no_doc_directory_is_a_confirmed_absence(self, collector):
        tree = RepoTree("o", "r", ["README.md", "src/main.c"], truncated=False)
        path, saw_gap = collector._find_doc_directory(tree)
        assert path is None
        assert saw_gap is False


class TestFindDocumentationSiteGapHandling:
    def _run(self, collector):
        import asyncio
        return asyncio.run(collector._find_documentation_site(None, "o", "r"))

    def test_gap_is_tracked_separately_from_confirmed_absence(self, collector):
        from unittest.mock import AsyncMock, patch
        with patch.object(collector, "_github_get", new=AsyncMock(return_value=COLLECTION_GAP)):
            site, saw_gap = self._run(collector)
        assert site is None
        assert saw_gap is True

    def test_confirmed_repo_with_no_homepage_or_pages_is_not_a_gap(self, collector):
        from unittest.mock import AsyncMock, patch
        with patch.object(collector, "_github_get", new=AsyncMock(return_value={"homepage": "", "has_pages": False})):
            site, saw_gap = self._run(collector)
        assert site is None
        assert saw_gap is False
