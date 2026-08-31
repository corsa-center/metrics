"""Unit tests for CollaborationCollector (CASS Section 4.2.7)."""

import pytest

from collectors.sustainability.collaboration import CollaborationCollector


@pytest.fixture
def collector():
    return CollaborationCollector()


def _pkg(ecosystem, name, deps=0, repos=0, install=None):
    return {
        "ecosystem": ecosystem, "name": name,
        "dependent_packages": deps, "dependent_repos": repos,
        "install_command": install, "registry_url": None,
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
