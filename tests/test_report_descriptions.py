"""The report's prose descriptions of what a pattern looks for (issue #99).

Where a pattern is too intricate to build from a word list, the report
describes it in words, and the description sits beside the pattern. These
tests take each example a description names and check the pattern really
matches it (and, where the description implies a limit, that the pattern
honors it), so editing a pattern without its description fails here
instead of leaving the report naming the wrong files.
"""

import re

import pytest

from collectors.common import CONTAINER_FILE_PATTERNS, ENVIRONMENT_SPEC_PATTERN
from collectors.ecosystem.community_health import _DEEP_DOC_PATTERNS
from collectors.ecosystem.outreach import _GETTING_STARTED_PATTERN, _SEARCH_DESCRIPTIONS
from collectors.ecosystem.welcomeness import _DECISION_PATTERNS, _PATTERN_DESCRIPTIONS
from collectors.quality.accessibility import _OTHER_BUILD_LOOKED_FOR, _OTHER_BUILD_PATTERNS
from collectors.quality.reliability import _HARDENING_LOOKED_FOR, _HARDENING_MARKERS
from collectors.quality.reproducibility import _TREE_PATTERNS, _TREE_SEARCHES


def _matches(pattern, text):
    if isinstance(pattern, str):
        pattern = re.compile(pattern, re.I)
    return bool(pattern.search(text))


class TestHardeningMarkers:
    def test_every_marker_is_described(self):
        assert set(_HARDENING_LOOKED_FOR) == set(_HARDENING_MARKERS)

    @pytest.mark.parametrize("label,example", [
        ("Warnings as errors", "-Werror"),
        ("Warnings as errors", "set(CMAKE_COMPILE_WARNING_AS_ERROR ON)"),
        ("Warnings as errors", "option(HDF5_ENABLE_WARNINGS_AS_ERRORS ...)"),
        ("Fortify source", "-D_FORTIFY_SOURCE=2"),
        ("Stack protector", "-fstack-protector-strong"),
        ("Sanitizers", "-fsanitize=address"),
        ("Sanitizers", "SUNDIALS_ENABLE_ADDRESS_SANITIZER"),
        ("Sanitizers", "ENABLE_ASAN"),
        ("CERT / MISRA reference", "follows CERT C"),
        ("CERT / MISRA reference", "MISRA compliance"),
    ])
    def test_described_examples_match(self, label, example):
        assert _matches(_HARDENING_MARKERS[label], example)


class TestOtherBuildSystems:
    @pytest.mark.parametrize("path", [
        "meson.build", "fpm.toml", "packages/mylib/package.py", "envs/spack.yaml",
        "src/CMakeLists.txt", "configure", "src/configure.ac", "install.sh", "build.sh",
    ])
    def test_described_examples_match(self, path):
        assert any(_matches(p, path) for _, p in _OTHER_BUILD_PATTERNS), path

    @pytest.mark.parametrize("path", ["sub/meson.build", "a/b/CMakeLists.txt", "scripts/install.sh"])
    def test_described_limits_hold(self, path):
        # "a root meson.build", "a CMakeLists.txt one directory down", "a root install.sh".
        assert not any(_matches(p, path) for _, p in _OTHER_BUILD_PATTERNS), path

    def test_description_names_each_kind(self):
        for word in ("meson.build", "fpm.toml", "Spack", "CMakeLists.txt", "configure",
                     "install.sh", "build.sh"):
            assert word in _OTHER_BUILD_LOOKED_FOR


class TestReproducibilityTreeSearches:
    def test_every_tree_search_is_described(self):
        assert set(_TREE_SEARCHES) == set(_TREE_PATTERNS)

    @pytest.mark.parametrize("key,path", [
        (("containers", "Dockerfile"), "Dockerfile"),
        (("containers", "Dockerfile"), "Containerfile"),
        (("containers", "Dockerfile"), "docker/Dockerfile.cuda"),
        (("containers", "Dockerfile"), "ci/gpu.dockerfile"),
        (("containers", "docker-compose"), "compose.yml"),
        (("containers", "docker-compose"), "deploy/docker-compose.yml"),
        (("containers", "Singularity / Apptainer"), "Singularity"),
        (("containers", "Singularity / Apptainer"), "containers/apptainer-gpu.def"),
        (("reproducibility_docs", "Environment specification"), "environment.yml"),
        (("reproducibility_docs", "Environment specification"), "ci/conda-env-dev.yml"),
        (("reproducibility_docs", "Environment specification"), "scripts/spack.yaml"),
        (("reproducibility_docs", "Environment specification"), ".devcontainer/devcontainer.json"),
        (("reproducibility_docs", "Environment specification"), ".uberenv_config.json"),
        (("reproducibility_docs", "Environment specification"), "shell.nix"),
        (("reproducibility_docs", "Environment specification"), "flake.nix"),
        (("reproducibility_docs", "Environment specification"), "pixi.toml"),
        (("reproducibility_docs", "Environment specification"), "spack/repo/packages/mylib/package.py"),
    ])
    def test_described_examples_match(self, key, path):
        assert _matches(_TREE_PATTERNS[key], path), path

    def test_docs_environment_is_excluded_as_described(self):
        # "environment*.yml ... (not under docs/)"
        assert not _matches(ENVIRONMENT_SPEC_PATTERN, "docs/environment.yml")


class TestOutreachSearches:
    @pytest.mark.parametrize("path", [
        "docs/CONTRIBUTING.md", "doc/contributing.rst", "src/docs/dev/contributing.md",
        "source/doc/contributors_guide.md",
    ])
    def test_contributing_guide_examples_match(self, path):
        assert "docs/ or doc/" in _SEARCH_DESCRIPTIONS["Contributing guide"][0]
        assert _matches(_DEEP_DOC_PATTERNS["contributing_guidelines"], path), path

    @pytest.mark.parametrize("path", [
        "docs/getting-started.md", "doc/quickstart.rst", "documentation/user/tutorial.md",
        "GETTING_STARTED.md", "tutorials/intro.md", "quick-start/README.md",
    ])
    def test_getting_started_examples_match(self, path):
        assert _matches(_GETTING_STARTED_PATTERN, path), path


class TestWelcomenessDocuments:
    def test_every_described_search_is_a_pattern(self):
        assert set(_PATTERN_DESCRIPTIONS) <= set(_DECISION_PATTERNS)

    @pytest.mark.parametrize("label,path", [
        ("Roadmap", "ROADMAP.md"), ("Roadmap", "docs/project-roadmap.rst"), ("Roadmap", "roadmap.txt"),
        ("Meeting notes", "meeting-notes/2024-01.md"), ("Meeting notes", "docs/meeting_notes.txt"),
        ("Meeting notes", "community/team-meeting-notes.md"),
    ])
    def test_described_examples_match(self, label, path):
        assert _matches(_DECISION_PATTERNS[label], path), path
