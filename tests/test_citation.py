"""Unit tests for CitationMetricCollector DOI discovery (CASS Section 4.1.1)."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from formatters.dashboard import DashboardFormatter

from collectors.impact.citation import CitationMetricCollector
from tests.fakes import FakeForge


@pytest.fixture
def collector():
    return CitationMetricCollector({"api_credentials": {}}, FakeForge())


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
        collector.openalex.citing_works = AsyncMock(return_value={
            "count": distinct, "work_ids": ["W1"] if distinct is not None else [],
            "unresolved": [], "failed": False})
        collector.openalex.get_work_citations = AsyncMock(return_value={"cited_by_count": per_doi})
        collector.semantic_scholar.get_citations = AsyncMock(return_value={"citation_count": ss})

    def test_distinct_citing_works_across_declared_dois(self, collector):
        self._stub(collector, distinct=602, ss=10)
        pkg = {"name": "amrex", "dois": ["10.1/a", "10.1/b"], "doi": "10.1/a"}
        assert asyncio.run(collector._get_formal_citations(pkg)) == 602
        collector.openalex.citing_works.assert_awaited_once_with(["10.1/a", "10.1/b"])

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
        collector.openalex.citing_works.assert_not_awaited()


class TestDeclaredDois:
    def test_reads_citation_cff(self, collector):
        import yaml
        collector.forge.files = {"CITATION.cff": yaml.safe_dump(AMREX_CFF)}
        out = asyncio.run(collector._declared_dois({"repo_url": "https://github.com/o/r"}))
        assert out["software"] == "10.5281/zenodo.2555438"

    def test_unreadable_or_missing_file(self, collector):
        out = asyncio.run(collector._declared_dois({"repo_url": "https://github.com/o/r"}))
        assert (out["software"], out["all"]) == (None, [])
        collector.forge.files = {"CITATION.cff": ": : not yaml ["}
        out = asyncio.run(collector._declared_dois({"repo_url": "https://github.com/o/r"}))
        assert (out["software"], out["all"]) == (None, [])


SUNDIALS_CITATIONS_MD = """# Citing SUNDIALS
```bibtex
@article{gardner2022sundials,
  doi       = {10.1145/3539801},
  url       = {https://doi.org/10.1145/3539801}
}
@article{hindmarsh2005sundials,
  doi       = {10.1145/1089014.1089020},
}
```
See also (https://doi.org/10.1145/3594632).
"""


class TestBibtexCitationFileFallback:
    def test_dois_from_text(self, collector):
        assert collector._dois_from_text(SUNDIALS_CITATIONS_MD) == [
            "10.1145/3539801", "10.1145/1089014.1089020", "10.1145/3594632",
        ]

    def test_used_when_there_is_no_citation_cff(self, collector):
        collector.forge.files = {"CITATIONS.md": SUNDIALS_CITATIONS_MD}
        out = asyncio.run(collector._declared_dois({"repo_url": "https://github.com/o/r"}))
        assert out["software"] is None
        assert out["all"][0] == "10.1145/3539801"

    def test_citation_cff_dois_take_precedence(self, collector):
        import yaml
        collector.forge.files = {"CITATION.cff": yaml.safe_dump(AMREX_CFF),
                                 "CITATIONS.md": SUNDIALS_CITATIONS_MD}
        out = asyncio.run(collector._declared_dois({"repo_url": "https://github.com/o/r"}))
        assert out["software"] == "10.5281/zenodo.2555438"
        assert [c[1] for c in collector.forge.calls if c[0] == "raw_text"] == ["CITATION.cff"]


class TestCitationEvidence:
    """Where Formal Citations came from (#99)."""

    def test_openalex_count_links_to_the_citing_works(self):
        from collectors.impact.citation import _citation_evidence
        declared = {"all": ["10.1/a", "10.1/b"], "sources": {"10.1/a": "CITATION.cff",
                                                             "10.1/b": "CITATION.cff"}}
        trace = {"openalex": {"count": 602, "work_ids": ["W1", "W2"], "unresolved": []},
                 "semantic_scholar": {"status": "found", "via": "doi", "citation_count": 10,
                                      "title": "AMReX", "year": 2019, "paper_id": "abc"}}
        ev = _citation_evidence(declared, trace, "amrex")
        assert ev["found"][0] == {"text": "602 distinct works citing these 2 DOIs", "path": "OpenAlex",
                                  "url": "https://openalex.org/works?filter=cites:W1|W2"}
        assert ev["found"][1]["url"] == "https://www.semanticscholar.org/paper/abc"
        assert ev["found"][1]["quote"] == "AMReX (2019)"
        assert {"text": "Formal Citations is the larger of these counts."} in ev["notes"]
        assert ev["searched"][0]["paths"][0] == {"path": "10.1/a (from CITATION.cff)",
                                                 "url": "https://doi.org/10.1/a"}

    def test_title_search_is_flagged_and_no_doi_says_where_it_looked(self):
        from collectors.impact.citation import _citation_evidence
        declared = {"all": [], "sources": {}, "looked_in": ["the catalog entry", "CITATION.cff"]}
        trace = {"semantic_scholar": {"status": "found", "via": "title", "query": "hdf5",
                                      "citation_count": 3, "title": "Some paper", "paper_id": "p"}}
        ev = _citation_evidence(declared, trace, "hdf5")
        assert "top result for a title search on “hdf5”" in ev["found"][0]["text"]
        assert any("can match an unrelated paper" in n["text"] for n in ev["notes"])
        assert ev["missing"] == [{"text": "a DOI to look citations up by"}]
        assert ev["searched"] == [{"text": "DOIs looked for in",
                                   "paths": [{"path": "the catalog entry"}, {"path": "CITATION.cff"}]}]

    def test_unresolved_doi_is_named(self):
        from collectors.impact.citation import _citation_evidence
        ev = _citation_evidence({"all": ["10.1/x"], "sources": {}},
                                {"openalex": {"count": None, "work_ids": [], "unresolved": ["10.1/x"]}}, "x")
        assert ev["missing"] == [{"text": "10.1/x in OpenAlex (it has no work for this DOI)"}]


class TestFailedLookupsAreNotCollected:
    def test_every_source_failing_is_none_not_zero(self, collector):
        collector.openalex.citing_works = AsyncMock(return_value={
            "count": None, "work_ids": [], "unresolved": [], "failed": True})
        collector.openalex.get_work_citations = AsyncMock(return_value={"cited_by_count": 0, "status": "failed"})
        collector.semantic_scholar.get_citations = AsyncMock(return_value={"citation_count": 0, "status": "failed"})
        assert asyncio.run(collector._get_formal_citations({"name": "x", "dois": ["10.1/a"]})) is None

    def test_one_source_answering_is_enough(self, collector):
        collector.openalex.citing_works = AsyncMock(return_value={
            "count": None, "work_ids": [], "unresolved": [], "failed": True})
        collector.openalex.get_work_citations = AsyncMock(return_value={"cited_by_count": 0, "status": "failed"})
        collector.semantic_scholar.get_citations = AsyncMock(return_value={"citation_count": 0, "status": "not_found"})
        assert asyncio.run(collector._get_formal_citations({"name": "x", "dois": ["10.1/a"]})) == 0


class TestFormalCitationsRow:
    def _data(self, formal, registries=None, downloads=None):
        from orchestrator import MetricsOrchestrator
        orch = MetricsOrchestrator(config_path="config/orchestrator.yaml")
        metrics = {"dimensions": {
            "impact": {"sub_results": {"score": 10.0, "sub_metrics": {"formal_citations": formal}}},
            "ecosystem": {"sub_results": {"collaboration": {
                "registries": registries or [], "package_downloads": downloads or {}}}},
            "quality": {"sub_results": {}}}}
        return DashboardFormatter().format(metrics, {})["impact"]["citation_and_adoption"]["data"]

    def test_zero_is_shown(self):
        assert "Formal Citations:</strong> 0</p>" in self._data({"raw_value": 0})

    def test_failed_lookup_is_unavailable_this_run(self):
        assert "Formal Citations:</strong> Unavailable this run" in self._data(
            {"raw_value": None, "not_collected": True})

    def test_downloads_detail_names_each_package(self):
        data = self._data({"raw_value": 1}, downloads={"total": 3, "by_registry": [
            {"ecosystem": "conda", "name": "hdf5", "downloads": 2, "period": "total"},
            {"ecosystem": "conda", "name": "hdf5-static", "downloads": 1, "period": "total"}]})
        assert "hdf5 (conda) 2 (total), hdf5-static (conda) 1 (total)" in data

    def test_reverse_dependency_evidence_names_the_package_behind_each_figure(self):
        from collectors.ecosystem.collaboration import CollaborationCollector
        ev = CollaborationCollector._reverse_dependency_evidence([
            {"name": "hdf5", "ecosystem": "spack", "dependent_packages": 222, "dependent_repos": 0},
            {"name": "hdf5", "ecosystem": "conda", "dependent_packages": 176, "dependent_repos": 1709,
             "registry_url": "https://anaconda.org/anaconda/hdf5"}])
        assert ev["found"] == [
            {"text": "222 dependent packages", "path": "hdf5 (spack)", "url": None},
            {"text": "1,709 dependent repositories", "path": "hdf5 (conda)",
             "url": "https://anaconda.org/anaconda/hdf5"}]


def test_semantic_scholar_without_the_paper_says_so():
    from collectors.impact.citation import _citation_evidence
    ev = _citation_evidence({"all": ["10.1/a"], "sources": {}},
                            {"semantic_scholar": {"status": "not_found", "citation_count": 0}}, "x")
    assert {"text": "a paper for the first DOI in Semantic Scholar"} in ev["missing"]


class TestMentionsAndDoiResolutions:
    def test_failed_mentions_search_is_not_collected(self, collector):
        collector.semantic_scholar.get_mentions = AsyncMock(return_value=None)
        assert asyncio.run(collector._get_informal_mentions({"name": "x"})) is None

    def test_failed_zenodo_lookup_is_not_collected(self, collector):
        collector.zenodo.get_doi_stats = AsyncMock(return_value={"downloads": 0, "views": 0, "status": "failed"})
        assert asyncio.run(collector._get_doi_resolutions({"doi": "10.5281/zenodo.1"})) is None

    def test_mentions_evidence_links_the_search(self):
        from collectors.impact.citation import _mentions_evidence
        ev = _mentions_evidence({"query": '"AMReX" software', "count": 42})
        assert ev["found"][0]["text"] == '42 papers returned by a search for "AMReX" software'
        assert ev["found"][0]["url"] == "https://www.semanticscholar.org/search?q=%22AMReX%22%20software"

    def test_zenodo_evidence_names_the_record_or_its_absence(self):
        from collectors.impact.citation import _zenodo_evidence
        found = _zenodo_evidence({"status": "found", "record_id": 7, "downloads": 5, "views": 9, "doi": "d"})
        assert found["found"][0]["url"] == "https://zenodo.org/records/7"
        assert _zenodo_evidence({"status": "not_found", "doi": "10.1/x"}) == {
            "missing": [{"text": "a Zenodo record for 10.1/x"}]}
