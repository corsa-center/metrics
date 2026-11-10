"""Tests for tools/spack_dependencies.py's recipe matching (dashboard #124).

The analysis itself runs under `spack python`; the matching is plain
Python, so it is tested here against recipes described by their URLs.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.spack_dependencies import match_recipes, recipe_urls, repo_location

# What Spack's recipes say, trimmed to the URLs that matter.
RECIPES = {
    "pumi": ["https://www.scorec.rpi.edu/pumi", "https://github.com/SCOREC/core.git"],
    "omega-h": ["https://github.com/sandialabs/omega_h",
                "https://github.com/sandialabs/omega_h/archive/v9.34.1.tar.gz",
                "https://github.com/SCOREC/omega_h.git"],
    "hdf5": ["https://www.hdfgroup.org/", "https://github.com/HDFGroup/hdf5.git"],
    "llvm": ["https://github.com/llvm/llvm-project"],
    "llvm-openmp": ["https://github.com/llvm/llvm-project/releases/download/x/openmp.tar.xz"],
    "aotriton-llvm": ["https://github.com/llvm/llvm-project.git"],
    "pcms": ["https://github.com/SCOREC/pcms"],
    "adios2": ["https://github.com/ornladios/ADIOS2"],
    "core-utils": ["https://github.com/other/core-utils"],
}
COUNTS = {"llvm": 300, "llvm-openmp": 20, "aotriton-llvm": 1}


def _match(*urls):
    return match_recipes(urls, RECIPES, COUNTS)


class TestRepoLocation:
    def test_forms_of_one_repository_compare_equal(self):
        assert {repo_location(u) for u in (
            "https://github.com/SCOREC/core", "https://github.com/scorec/core.git/",
            "git@github.com:SCOREC/core.git", "git://github.com/SCOREC/core",
        )} == {"github.com/scorec/core"}

    def test_garbage(self):
        assert repo_location("") is None
        assert repo_location("https://github.com/") is None


class TestMatchRecipes:
    def test_recipe_named_differently_from_the_repository(self):
        assert _match("https://github.com/SCOREC/core") == {"https://github.com/SCOREC/core": "pumi"}

    def test_a_version_fetched_from_a_fork(self):
        # Spack's omega-h builds its -scorec versions from SCOREC/omega_h.
        assert _match("https://github.com/SCOREC/omega_h")["https://github.com/SCOREC/omega_h"] == "omega-h"

    def test_download_below_the_repository(self):
        assert _match("https://github.com/sandialabs/omega_h")["https://github.com/sandialabs/omega_h"] == "omega-h"

    def test_monorepo_prefers_the_most_depended_on(self):
        assert _match("https://github.com/llvm/llvm-project")["https://github.com/llvm/llvm-project"] == "llvm"

    def test_a_recipe_named_after_the_repository_wins(self):
        recipes = {**RECIPES, "llvm-project": ["https://github.com/llvm/llvm-project"]}
        result = match_recipes(["https://github.com/llvm/llvm-project"], recipes, COUNTS)
        assert result["https://github.com/llvm/llvm-project"] == "llvm-project"

    def test_a_longer_repository_name_is_not_a_match(self):
        # github.com/scorec/core must not match github.com/scorec/core-extra.
        recipes = {"core-extra": ["https://github.com/SCOREC/core-extra"]}
        assert match_recipes(["https://github.com/SCOREC/core"], recipes, {}) == {
            "https://github.com/SCOREC/core": None}

    def test_falls_back_to_the_name(self):
        recipes = {"hdf5": ["https://support.hdfgroup.org/ftp/HDF5"]}
        assert match_recipes(["https://github.com/HDFGroup/HDF5"], recipes, {}) == {
            "https://github.com/HDFGroup/HDF5": "hdf5"}

    def test_name_fallback_reads_underscore_as_hyphen(self):
        recipes = {"foo-bar": ["https://example.org/foo-bar"]}
        assert match_recipes(["https://gitlab.com/g/foo_bar"], recipes, {}) == {
            "https://gitlab.com/g/foo_bar": "foo-bar"}

    def test_not_in_spack(self):
        assert _match("https://github.com/SCOREC/pumi-pic") == {"https://github.com/SCOREC/pumi-pic": None}

    def test_case_and_dot_git_differences(self):
        assert _match("https://github.com/ORNLADIOS/adios2.git")["https://github.com/ORNLADIOS/adios2.git"] == "adios2"


class TestRecipeUrls:
    def test_class_and_version_urls(self):
        pkg = SimpleNamespace(
            homepage="https://h", url="https://u", urls=["https://a", "https://b"], git=None,
            list_url=None,
            versions={"1.0": {"sha256": "x"}, "2.0-scorec": {"git": "https://github.com/SCOREC/omega_h.git"},
                      "3.0": {"url": "https://v3"}},
        )
        assert recipe_urls(pkg) == ["https://h", "https://u", "https://a", "https://b",
                                    "https://github.com/SCOREC/omega_h.git", "https://v3"]

    def test_an_attribute_that_raises_is_skipped(self):
        class Pkg:
            versions = {}
            git = "https://g"

            @property
            def homepage(self):
                raise RuntimeError

        assert recipe_urls(Pkg()) == ["https://g"]
