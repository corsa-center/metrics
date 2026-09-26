"""Unit tests for CollaborationCollector (CASS Section 4.2.7)."""

import asyncio
import pytest

from collectors.ecosystem.collaboration import CollaborationCollector


@pytest.fixture
def collector():
    return CollaborationCollector()


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
        sub = collector._calculate_score([])["sub_scores"]
        assert sum(1 for v in sub.values() if v.get("not_collected")) == 3

    def test_max_score_excludes_the_4_3_4_row(self, collector):
        # installation_success belongs to 4.3.4 and must not inflate 4.2.7.
        s = collector._calculate_score([
            _pkg("conda", "x", deps=99, install="conda install x"),
            _pkg("spack", "x", install="spack install x"),
        ])
        assert s["max_score"] == 5
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
