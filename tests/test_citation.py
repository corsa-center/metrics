"""Unit tests for CitationMetricCollector DOI discovery (CASS Section 4.1.1)."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from collectors.impact.citation import CitationMetricCollector


@pytest.fixture
def collector():
    return CitationMetricCollector({"api_credentials": {}})


# Trimmed from AMReX's CITATION.cff: a Zenodo concept DOI for the software,
# the JOSS paper as preferred-citation, and two more papers as references.
AMREX_CFF = {
    "title": "AMReX",
    "doi": "10.5281/zenodo.2555438",
    "preferred-citation": {"type": "article", "doi": "10.21105/joss.01370"},
    "references": [
        {"type": "article", "doi": "10.1177/10943420211022811"},
        {"type": "article", "identifiers": [
            {"type": "doi", "value": "https://doi.org/10.1177/10943420241271017"},
            {"type": "url", "value": "https://example.org"},
        ]},
    ],
}


class TestDoisFromCff:
    def test_collects_software_preferred_and_reference_dois(self, collector):
        out = collector._dois_from_cff(AMREX_CFF)
        assert out["software"] == "10.5281/zenodo.2555438"
        assert out["all"] == [
            "10.5281/zenodo.2555438", "10.21105/joss.01370",
            "10.1177/10943420211022811", "10.1177/10943420241271017",
        ]

    def test_catalog_doi_comes_first_and_is_deduplicated(self, collector):
        out = collector._dois_from_cff(AMREX_CFF, "https://doi.org/10.21105/JOSS.01370")
        assert out["all"][0] == "10.21105/joss.01370"
        assert out["all"].count("10.21105/joss.01370") == 1

    def test_no_software_doi(self, collector):
        out = collector._dois_from_cff({"preferred-citation": {"doi": "10.1234/x"}})
        assert out["software"] is None
        assert out["all"] == ["10.1234/x"]

    def test_non_doi_values_are_ignored(self, collector):
        out = collector._dois_from_cff({"doi": "not a doi", "references": ["junk", None]})
        assert out == {"software": None, "all": []}


class TestFormalCitations:
    def _stub(self, collector, distinct, per_doi=0, ss=0):
        collector.openalex.count_citing_works = AsyncMock(return_value=distinct)
        collector.openalex.get_work_citations = AsyncMock(return_value={"cited_by_count": per_doi})
        collector.semantic_scholar.get_citations = AsyncMock(return_value={"citation_count": ss})

    def test_distinct_citing_works_across_declared_dois(self, collector):
        self._stub(collector, distinct=602, ss=10)
        pkg = {"name": "amrex", "dois": ["10.1/a", "10.1/b"], "doi": "10.1/a"}
        assert asyncio.run(collector._get_formal_citations(pkg)) == 602
        collector.openalex.count_citing_works.assert_awaited_once_with(["10.1/a", "10.1/b"])

    def test_falls_back_to_best_single_doi(self, collector):
        self._stub(collector, distinct=None, per_doi=120)
        pkg = {"name": "amrex", "dois": ["10.1/a", "10.1/b"]}
        assert asyncio.run(collector._get_formal_citations(pkg)) == 120

    def test_semantic_scholar_queried_by_doi_when_declared(self, collector):
        self._stub(collector, distinct=5)
        pkg = {"name": "amrex", "dois": ["10.1/a"]}
        asyncio.run(collector._get_formal_citations(pkg))
        collector.semantic_scholar.get_citations.assert_awaited_once_with("10.1/a", "amrex")

    def test_no_doi_uses_title_search_only(self, collector):
        self._stub(collector, distinct=999, ss=7)
        assert asyncio.run(collector._get_formal_citations({"name": "amrex"})) == 7
        collector.openalex.count_citing_works.assert_not_awaited()


class TestDeclaredDois:
    def test_reads_citation_cff(self, collector):
        import yaml
        collector.github.get_file_content = AsyncMock(return_value=yaml.safe_dump(AMREX_CFF))
        out = asyncio.run(collector._declared_dois({"repo_url": "https://github.com/o/r"}))
        assert out["software"] == "10.5281/zenodo.2555438"

    def test_unreadable_or_missing_file(self, collector):
        collector.github.get_file_content = AsyncMock(return_value=None)
        out = asyncio.run(collector._declared_dois({"repo_url": "https://github.com/o/r"}))
        assert out == {"software": None, "all": []}
        collector.github.get_file_content = AsyncMock(return_value=": : not yaml [")
        out = asyncio.run(collector._declared_dois({"repo_url": "https://github.com/o/r"}))
        assert out == {"software": None, "all": []}
