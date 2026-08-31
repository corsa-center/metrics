"""Unit tests for UsabilityCollector (CASS Section 4.3.4)."""

import pytest

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
        assert self._score(collector)["max_score"] == 4


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
