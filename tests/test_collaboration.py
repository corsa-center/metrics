"""Unit tests for CollaborationCollector (CASS Section 4.2.7)."""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from collectors.ecosystem.collaboration import CollaborationCollector
from tests.fakes import FakeForge


@pytest.fixture
def collector():
    # None: every test here exercises pure computation methods
    # (_merge/_calculate_score/_downloads_summary) that never touch
    # self.forge.
    return CollaborationCollector(FakeForge())


def _pkg(ecosystem, name, deps=0, repos=0, install=None, downloads=0, downloads_period=None):
    return {
        "ecosystem": ecosystem, "name": name,
        "dependent_packages": deps, "dependent_repos": repos,
        "install_command": install, "registry_url": None,
        "downloads": downloads, "downloads_period": downloads_period,
    }


class TestMerge:
    def test_duplicate_package_keeps_the_higher_counts(self, collector):
        # conda-forge and anaconda.org both index `hdf5`; summing would
        # double-count its dependents.
        merged = collector._merge([
            _pkg("conda", "hdf5", deps=176, repos=1709),
            _pkg("conda", "hdf5", deps=18, repos=1709),
        ])
        assert len(merged) == 1
        assert merged[0]["dependent_packages"] == 176

    def test_same_ecosystem_different_names_stay_separate(self, collector):
        merged = collector._merge([
            _pkg("conda", "hdf5"), _pkg("conda", "hdf5-static"),
        ])
        assert len(merged) == 2

    def test_install_command_survives_the_merge(self, collector):
        merged = collector._merge([
            _pkg("spack", "zfp", install=None),
            _pkg("spack", "zfp", install="spack install zfp"),
        ])
        assert merged[0]["install_command"] == "spack install zfp"

    def test_sorted_by_dependents_descending(self, collector):
        merged = collector._merge([_pkg("a", "x", deps=1), _pkg("b", "y", deps=99)])
        assert [m["ecosystem"] for m in merged] == ["b", "a"]


class TestScoring:
    def test_one_ecosystem_fails_dependency_analysis(self, collector):
        s = collector._calculate_score([_pkg("conda", "x")])
        assert not s["sub_scores"]["advanced_dependency_analysis"]["passing"]

    def test_two_ecosystems_pass(self, collector):
        s = collector._calculate_score([_pkg("conda", "x"), _pkg("spack", "x")])
        assert s["sub_scores"]["advanced_dependency_analysis"]["passing"]

    def test_network_passes_on_dependent_packages(self, collector):
        s = collector._calculate_score([_pkg("conda", "x", deps=10)])
        assert s["sub_scores"]["collaboration_network"]["passing"]

    def test_network_passes_on_dependent_repos_alone(self, collector):
        # zfp's shape: few dependent packages, many dependent repositories.
        s = collector._calculate_score([_pkg("conda", "zfp", deps=9, repos=111)])
        assert s["sub_scores"]["collaboration_network"]["passing"]

    def test_network_fails_when_both_are_low(self, collector):
        s = collector._calculate_score([_pkg("conda", "x", deps=3, repos=0)])
        assert not s["sub_scores"]["collaboration_network"]["passing"]

    def test_no_registries(self, collector):
        s = collector._calculate_score([])
        assert s["score"] == 0
        assert "Not packaged" in s["sub_scores"]["advanced_dependency_analysis"]["value"]

    def test_three_submetrics_uncollected(self, collector):
        # Plus the network row, unmeasured without a dependency-audit graph.
        sub = collector._calculate_score([])["sub_scores"]
        assert sum(1 for v in sub.values() if v.get("not_collected")) == 4
        sub = collector._calculate_score([], {"count": 0, "complete": True})["sub_scores"]
        assert sum(1 for v in sub.values() if v.get("not_collected")) == 3

    def test_max_score_excludes_the_4_3_4_row(self, collector):
        # installation_success belongs to 4.3.4 and must not inflate 4.2.7.
        s = collector._calculate_score([
            _pkg("conda", "x", deps=99, install="conda install x"),
            _pkg("spack", "x", install="spack install x"),
        ])
        # Only measured rows count; the three uncollected stubs aren't failures.
        assert s["max_score"] == 2
        assert s["score"] == 2
        assert s["sub_scores"]["installation_success"]["passing"]


class TestInstallationSuccess:
    def test_counts_distinct_ecosystems_not_packages(self, collector):
        # conda carrying both `hdf5` and `hdf5-static` is one package manager.
        s = collector._calculate_score([
            _pkg("conda", "hdf5", install="conda install hdf5"),
            _pkg("conda", "hdf5-static", install="conda install hdf5-static"),
            _pkg("spack", "hdf5", install="spack install hdf5"),
        ])
        value = s["sub_scores"]["installation_success"]["value"]
        assert value == "Installable from 2 package managers: conda, spack"

    def test_singular_wording(self, collector):
        s = collector._calculate_score([_pkg("spack", "x", install="spack install x")])
        assert "1 package manager: spack" in s["sub_scores"]["installation_success"]["value"]

    def test_registry_without_install_command_does_not_count(self, collector):
        s = collector._calculate_score([_pkg("go", "github.com/x/y")])
        assert not s["sub_scores"]["installation_success"]["passing"]


class TestDownloadsMerge:
    def test_duplicate_package_keeps_the_higher_download_count(self, collector):
        merged = collector._merge([
            _pkg("conda", "hdf5", downloads=3_000_000, downloads_period="total"),
            _pkg("conda", "hdf5", downloads=100, downloads_period="total"),
        ])
        assert merged[0]["downloads"] == 3_000_000

    def test_missing_downloads_key_defaults_to_zero(self, collector):
        # Raw package dicts elsewhere in this test module omit downloads
        # entirely; _merge must not KeyError on them.
        merged = collector._merge([
            {"ecosystem": "conda", "name": "x", "dependent_packages": 0,
             "dependent_repos": 0, "install_command": None, "registry_url": None},
        ])
        assert merged[0].get("downloads", 0) == 0


class TestDownloadsSummary:
    def test_totals_and_per_registry_breakdown(self, collector):
        summary = collector._downloads_summary([
            _pkg("conda", "hdf5", downloads=3_100_672, downloads_period="total"),
            _pkg("pypi", "h5py", downloads=41_742_012, downloads_period="last-month"),
        ])
        assert summary["total"] == 3_100_672 + 41_742_012
        assert len(summary["by_registry"]) == 2

    def test_zero_download_registries_are_omitted(self, collector):
        summary = collector._downloads_summary([_pkg("spack", "hdf5", downloads=0)])
        assert summary == {"total": 0, "by_registry": []}

    def test_empty_registries(self, collector):
        assert collector._downloads_summary([]) == {"total": 0, "by_registry": []}


class TestDropSpuriousGoEntries:
    """Go's decentralized module system lets any public repo be `go get`-ed
    without the project ever intending to publish a Go module -- ecosyste.ms
    indexes AMReX-Codes/amrex (a C++ library) under "go" with zero
    dependents purely because some tool once resolved that path.
    corsa-center/metrics#50.
    """

    def test_zero_dependent_go_entry_dropped_for_non_go_repo(self, collector):
        registries = [_pkg("go", "github.com/AMReX-Codes/amrex"), _pkg("spack", "amrex", deps=2)]
        result = collector._drop_spurious_go_entries(registries, "C++")
        assert [r["ecosystem"] for r in result] == ["spack"]

    def test_go_entry_with_real_dependents_kept(self, collector):
        registries = [_pkg("go", "github.com/foo/bar", deps=5)]
        result = collector._drop_spurious_go_entries(registries, "C++")
        assert len(result) == 1

    def test_go_entry_kept_for_a_real_go_project(self, collector):
        # A genuinely young Go package can legitimately have zero
        # dependents yet -- only drop the noise for non-Go repos.
        registries = [_pkg("go", "github.com/foo/bar")]
        result = collector._drop_spurious_go_entries(registries, "Go")
        assert len(result) == 1

    def test_unknown_primary_language_keeps_the_entry(self, collector):
        # Absence of information isn't license to discard real data.
        registries = [_pkg("go", "github.com/foo/bar")]
        result = collector._drop_spurious_go_entries(registries, None)
        assert len(result) == 1

    def test_non_go_ecosystems_never_touched(self, collector):
        registries = [_pkg("pypi", "foo")]
        result = collector._drop_spurious_go_entries(registries, "C++")
        assert len(result) == 1


class _FakeJson:
    """Stand-in for _get_json keyed by URL substring."""

    def __init__(self, responses):
        self.responses = responses
        self.urls = []

    async def __call__(self, client, url):
        self.urls.append(url)
        for key, value in self.responses.items():
            if key in url:
                return value
        return None


class TestCondaForgeLookup:
    AMREX = {"name": "amrex", "dev_url": "https://github.com/AMReX-Codes/amrex",
             "home": "https://amrex-codes.github.io/amrex/", "source_git_url": None,
             "html_url": "http://anaconda.org/conda-forge/amrex", "ndownloads": 1234}

    def test_linked_package_is_credited(self, collector):
        collector._get_json = _FakeJson({"conda-forge/amrex": self.AMREX})
        rec = asyncio.run(collector._lookup_conda_forge(None, "AMReX-Codes", "amrex"))
        assert rec["ecosystem"] == "conda"
        assert rec["install_command"] == "conda install -c conda-forge amrex"
        assert rec["downloads"] == 1234

    def test_same_name_unrelated_package_is_not_credited(self, collector):
        other = {**self.AMREX, "dev_url": "https://github.com/someone-else/amrex",
                 "home": "https://example.org"}
        collector._get_json = _FakeJson({"conda-forge/amrex": other})
        assert asyncio.run(collector._lookup_conda_forge(None, "AMReX-Codes", "amrex")) is None

    def test_absent_package(self, collector):
        collector._get_json = _FakeJson({})
        assert asyncio.run(collector._lookup_conda_forge(None, "o", "r")) is None

    def test_points_at_normalizes_url_forms(self, collector):
        t = "github.com/amrex-codes/amrex"
        for link in ["https://github.com/AMReX-Codes/amrex", "http://www.github.com/amrex-codes/amrex/",
                     "https://github.com/AMReX-Codes/amrex.git", "https://github.com/AMReX-Codes/amrex/tree/dev"]:
            assert collector._points_at(link, t), link
        assert not collector._points_at("https://github.com/AMReX-Codes/amrex-tutorials", t)


class TestSpackDependents:
    def test_spack_index_count_wins_when_higher(self, collector):
        collector._get_json = _FakeJson({
            "spack.io/packages/amrex": {"name": "amrex", "ecosystem": "spack",
                                        "dependent_packages_count": 2},
            "packages.spack.io/data/packages/amrex.json": {
                "dependent_to": [{"name": n} for n in ["erf", "warpx", "py-amrex", "xsdk", "fastmath", "truchas-pbf"]]},
        })
        rec = asyncio.run(collector._lookup_spack(None, "amrex"))
        assert rec["dependent_packages"] == 6

    def test_ecosystems_count_kept_when_spack_index_unavailable(self, collector):
        collector._get_json = _FakeJson({
            "spack.io/packages/amrex": {"name": "amrex", "ecosystem": "spack",
                                        "dependent_packages_count": 2},
        })
        assert asyncio.run(collector._lookup_spack(None, "amrex"))["dependent_packages"] == 2


class TestSourceLevelDependents:
    def test_audit_count_can_carry_the_network_row(self, collector):
        s = collector._calculate_score([_pkg("spack", "amrex", deps=6)], {"count": 64, "complete": True})
        row = s["sub_scores"]["collaboration_network"]
        assert row["passing"] is True
        assert row["value"] == "6 dependent packages, 64 dependent repositories"
        assert row["detail"] == "64 source-level dependents found by dependency audit"

    def test_without_an_audit_a_registry_shortfall_is_unmeasured(self, collector):
        row = collector._calculate_score([_pkg("spack", "amrex", deps=6)])["sub_scores"]["collaboration_network"]
        assert row["not_collected"] is True and row["unmeasured"] is True
        assert row["value"].endswith("source-level dependents not audited")

    def test_registry_pass_stands_without_an_audit(self, collector):
        row = collector._calculate_score([_pkg("conda", "hdf5", deps=176)])["sub_scores"]["collaboration_network"]
        assert row["passing"] is True and "not_collected" not in row

    def _graph(self, collector, graph, owner="AMReX-Codes", repo="amrex"):
        from unittest.mock import AsyncMock, patch
        with patch.object(collector, "_get_json", new=AsyncMock(return_value=graph)):
            return asyncio.run(collector._source_dependents(None, owner, repo))

    def test_only_confident_external_depends_on_edges_count(self, collector):
        e = lambda src, conf="high", rel="DEPENDS_ON": {"source": src, "target": "amrex-codes/amrex",
                                                         "confidence": conf, "relationship": rel}
        graph = {"meta": {"root": "amrex-codes/amrex", "completeness": {"complete": True}}, "edges": [
            e("AMReX-Astro/Castro"), e("erf-model/ERF", "medium"), e("x/low", "low"),
            e("x/vendored", rel="VENDORED"), e("x/mirror", rel="MIRROR"),
            e("AMReX-Codes/amrex-tutorials")]}
        assert self._graph(collector, graph)["count"] == 2

    def test_unscored_graph_is_not_used(self, collector):
        # Older audit output: no confidence, generic-name false positives.
        graph = {"meta": {"root": "scorec/core"},
                 "edges": [{"source": "torvalds/linux", "target": "scorec/core"}]}
        assert self._graph(collector, graph, "SCOREC", "core") is None

    def test_graph_for_another_repository_is_not_used(self, collector):
        graph = {"meta": {"root": "someone/amrex"}, "edges": [
            {"source": "a/b", "target": "someone/amrex", "confidence": "high", "relationship": "DEPENDS_ON"}]}
        assert self._graph(collector, graph) is None


class TestSpackByRepository:
    def test_recipe_found_under_another_name(self, collector, monkeypatch):
        import collectors.ecosystem.collaboration as collab
        monkeypatch.setattr(collab, "_spack_by_repo", None)
        collector._get_json = _FakeJson({
            "repology.json": {"packages": {
                "pumi": {"homepages": ["https://www.scorec.rpi.edu/pumi"],
                         "version": [{"downloads": [["https://github.com/SCOREC/core.git"]]}]},
                "omega-h": {"homepages": ["https://github.com/sandialabs/omega_h"]},
            }},
            "spack.io/packages/pumi": {"name": "pumi", "ecosystem": "spack", "dependent_packages_count": 3},
        })
        rec = asyncio.run(collector._lookup_spack(None, "core", "SCOREC"))
        assert rec["name"] == "pumi"

    def test_a_fork_is_not_credited_with_upstreams_recipe(self, collector, monkeypatch):
        import collectors.ecosystem.collaboration as collab
        monkeypatch.setattr(collab, "_spack_by_repo", None)
        collector._get_json = _FakeJson({
            "repology.json": {"packages": {"omega-h": {"homepages": ["https://github.com/sandialabs/omega_h"]}}},
        })
        assert asyncio.run(collector._lookup_spack(None, "omega_h", "SCOREC")) is None

    def test_most_depended_on_recipe_wins(self, collector, monkeypatch):
        import collectors.ecosystem.collaboration as collab
        monkeypatch.setattr(collab, "_spack_by_repo", {"llvm/llvm-project": ["aotriton-llvm", "llvm"]})
        collector._get_json = _FakeJson({
            "data/packages/aotriton-llvm.json": {"dependent_to": [{"name": "aotriton"}]},
            "data/packages/llvm.json": {"dependent_to": [{"name": n} for n in "abcdef"]},
            "spack.io/packages/llvm": {"name": "llvm", "ecosystem": "spack", "dependent_packages_count": 6},
        })
        assert asyncio.run(collector._lookup_spack(None, "llvm-project", "llvm"))["name"] == "llvm"


class TestFetchFailure:
    def _get(self, collector, status):
        import httpx
        from unittest.mock import AsyncMock, patch
        transport = httpx.MockTransport(lambda request: httpx.Response(status, json={}))

        async def go():
            async with httpx.AsyncClient(transport=transport) as client:
                return await collector._get_json(client, "https://packages.example/x")
        with patch("collectors.ecosystem.collaboration.asyncio.sleep", new=AsyncMock()):
            return asyncio.run(go())

    def test_404_is_absent_and_anything_else_failed(self, collector):
        from collectors.ecosystem.collaboration import _FETCH_FAILED
        assert self._get(collector, 404) is None
        assert self._get(collector, 503) is _FETCH_FAILED
        assert self._get(collector, 403) is _FETCH_FAILED
        assert not _FETCH_FAILED


class TestLookupTraces:
    """Each lookup records what it searched, so the report can say (#99)."""

    def test_conda_records_names_and_an_unlinked_namesake(self, collector):
        other = {**TestCondaForgeLookup.AMREX, "dev_url": "https://github.com/someone-else/amrex",
                 "home": "https://example.org"}
        # Both spellings answer with the same package, as anaconda.org can.
        collector._get_json = _FakeJson({"conda-forge/amrex": other, "conda-forge/AMReX": other})
        trace = {}
        asyncio.run(collector._lookup_conda_forge(None, "AMReX-Codes", "AMReX", trace))
        assert trace == {"names": ["amrex", "AMReX"], "failed": False, "unlinked": [
            {"name": "amrex", "url": "http://anaconda.org/conda-forge/amrex"}]}

    def test_spack_records_names_tried_and_failures(self, collector):
        from collectors.ecosystem.collaboration import _FETCH_FAILED

        async def failing(client, url):
            return _FETCH_FAILED if url.endswith("/ADIOS2") else None
        collector._get_json = failing
        trace = {}
        assert asyncio.run(collector._lookup_spack(None, "ADIOS2", "ornladios", trace)) is None
        assert trace == {"names": ["ADIOS2", "adios2"], "failed": True, "by_download_url": True}

    def test_audit_graph_outcomes(self, collector):
        from unittest.mock import AsyncMock, patch
        from collectors.ecosystem.collaboration import _FETCH_FAILED
        for graph, outcome in [
            (None, "absent"), (_FETCH_FAILED, "failed"),
            ({"meta": {"root": "someone/amrex"}, "edges": []},
             "the graph published under this name is for another repository"),
        ]:
            trace = {}
            with patch.object(collector, "_get_json", new=AsyncMock(return_value=graph)):
                asyncio.run(collector._source_dependents(None, "AMReX-Codes", "amrex", trace))
            assert trace["outcome"] == outcome
            assert trace["path"] == ("corsa-center/project-dependent-tracking/"
                                     "amrex-dependency-context/dependency_graph.json")


class TestEvidence:
    LOOKUPS = {
        "repository": {"url": "https://github.com/o/r", "failed": False},
        "spack": {"names": ["R", "r"], "failed": False, "by_download_url": True},
        "conda": {"names": ["r", "R"], "failed": False, "unlinked": [{"name": "r", "url": None}]},
        "audit": {"path": "corsa-center/project-dependent-tracking/r-dependency-context/"
                          "dependency_graph.json", "outcome": "absent"},
    }

    def _sub(self, collector, registries, source=None, lookups=None):
        return collector._calculate_score(registries, source, lookups or self.LOOKUPS)["sub_scores"]

    def test_found_packages_link_to_their_registry_and_empty_registries_list_names_tried(self, collector):
        pkg = {**_pkg("pypi", "h5py", deps=3), "registry_url": "https://pypi.org/project/h5py"}
        ev = self._sub(collector, [pkg])["advanced_dependency_analysis"]["evidence"]
        assert ev["found"] == [{"path": "h5py (pypi)", "url": "https://pypi.org/project/h5py"}]
        assert [s["text"] for s in ev["searched"]] == [
            "Package registries searched (via ecosyste.ms) for packages whose repository URL is",
            "Spack recipes looked for by name", "conda-forge packages looked for by name"]
        assert ev["searched"][1]["paths"][-1]["path"].startswith("any recipe whose homepage")
        assert ev["missing"][0]["text"].startswith("a link to this repository from conda-forge's r ")

    def test_a_registry_already_credited_lists_no_search_for_it(self, collector):
        ev = self._sub(collector, [_pkg("spack", "r"), _pkg("conda", "r")])[
            "advanced_dependency_analysis"]["evidence"]
        assert [s["text"] for s in ev["searched"]] == [
            "Package registries searched (via ecosyste.ms) for packages whose repository URL is"]
        assert ev["missing"] == []

    def test_failed_lookups_claim_no_search(self, collector):
        lookups = {"repository": {"url": "https://github.com/o/r", "failed": True},
                   "spack": {"names": ["r"], "failed": True},
                   "conda": {"failed": True}, "audit": {"outcome": "failed"}}
        sub = self._sub(collector, [], lookups=lookups)
        for key in ("advanced_dependency_analysis", "collaboration_network"):
            assert sub[key]["evidence"] == {"found": [], "missing": [], "searched": []}

    def test_network_names_the_package_behind_each_count_and_the_graph(self, collector):
        source = {"count": 64, "complete": True, "url": "https://raw.example/graph.json"}
        ev = self._sub(collector, [_pkg("spack", "amrex", deps=6), _pkg("pypi", "x")], source)[
            "collaboration_network"]["evidence"]
        assert ev["found"] == [
            {"text": "amrex (spack): 6 dependent packages, 0 dependent repositories"},
            {"text": "64 source-level dependents", "url": "https://raw.example/graph.json",
             "path": self.LOOKUPS["audit"]["path"]},
        ]

    def test_network_without_a_graph_says_where_it_looked(self, collector):
        ev = self._sub(collector, [])["collaboration_network"]["evidence"]
        assert ev["missing"] == [{"text": "a dependency-audit graph for this repository"}]
        assert ev["searched"][0]["paths"] == [{"path": self.LOOKUPS["audit"]["path"]}]

    def test_report_shows_it_under_the_rows(self, collector):
        from orchestrator import MetricsOrchestrator
        from package_report import render_package_report
        pkg = {**_pkg("pypi", "h5py", deps=3), "registry_url": "https://pypi.org/project/h5py"}
        collaboration = {"overall_score": collector._calculate_score([pkg], None, self.LOOKUPS)}
        metrics = {"dimensions": {
            "impact": {"metadata": {"status": "placeholder"}},
            "ecosystem": {"sub_results": {"collaboration": collaboration}},
            "quality": {"sub_results": {}},
        }}
        orch = MetricsOrchestrator(config_path="config/orchestrator.yaml")
        dashboard = orch._transform_for_dashboard(metrics, {})
        page = render_package_report("o/r", dashboard, metrics, orch._metric_weights())

        deps = page.index("<b>Advanced Dependency Analysis:</b>")
        net = page.index("<b>Collaboration Network Analysis:</b>")
        found = page.index('<b>Found:</b> <a href="https://pypi.org/project/h5py">h5py (pypi)</a>')
        assert deps < found < net
        assert "<b>Spack recipes looked for by name:</b> R, r, any recipe" in page[deps:net]
        assert "<b>Found:</b> h5py (pypi): 3 dependent packages, 0 dependent repositories" in page[net:]
        assert "<b>Not found:</b> a dependency-audit graph for this repository" in page[net:]


class TestGoFilterUsesThePackagesLanguage:
    """The orchestrator records a repository's language as package["language"].
    The filter read "primary_language", which nothing sets since 2dd04d3, so
    HDF5 and Kokkos were credited with Go from pkg.go.dev entries."""

    def _collect(self, language):
        from tests.fakes import FakeForge
        go = {"ecosystem": "go", "name": "github.com/hDFgroup/hdf5", "dependent_packages_count": 0,
              "dependent_repos_count": 0, "install_command": "go get github.com/hDFgroup/hdf5"}
        spack = {"ecosystem": "spack", "name": "hdf5", "dependent_packages": 222, "dependent_repos": 0,
                 "install_command": "spack install hdf5", "registry_url": None}
        collector = CollaborationCollector(FakeForge())
        with patch.object(collector, "_lookup_by_repository",
                          new=AsyncMock(return_value=[collector._normalize(go)])), \
             patch.object(collector, "_lookup_spack", new=AsyncMock(return_value=spack)), \
             patch.object(collector, "_lookup_conda_forge", new=AsyncMock(return_value=None)), \
             patch.object(collector, "_source_dependents", new=AsyncMock(return_value=None)):
            return asyncio.run(collector.collect(
                {"name": "HDF5", "repo_url": "https://github.com/HDFGroup/hdf5", "language": language}))

    def test_go_entry_dropped_for_a_c_repository(self):
        sub = self._collect("C")["overall_score"]["sub_scores"]
        assert sub["installation_success"]["value"] == "Installable from 1 package manager: spack"

    def test_go_entry_kept_for_a_go_repository(self):
        sub = self._collect("Go")["overall_score"]["sub_scores"]
        assert "go" in sub["installation_success"]["value"]
