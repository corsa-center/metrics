"""Unit tests for Forge.for_repo, the one place a concrete forge is chosen
and set up (plus the orchestrator's thin per-package wrapper).

GitHubForge for github.com, GitLabForge for gitlab.com or a GitLab host,
None (skip the package) for anything else.
"""

import pytest

from forge.github import GitHubForge
from forge.gitlab import GitLabForge
from forge.interface import Forge
from orchestrator import MetricsOrchestrator

# The shape of config/orchestrator.yaml's api_credentials block.
CREDENTIALS = {
    "github": {"token": "gh-token"},
    "gitlab": {"gitlab.com": {"token": ""}, "gitlab.kitware.com": {"token": ""}},
}


@pytest.fixture
def resolve():
    """resolve(repo_url, repo_type=None), against CREDENTIALS."""
    return lambda repo_url, repo_type=None: Forge.for_repo(repo_type, repo_url, CREDENTIALS)


class TestKnownHosts:
    def test_github_com_resolves_to_github_forge(self, resolve):
        forge = resolve("https://github.com/HDFGroup/hdf5")
        assert isinstance(forge, GitHubForge)

    def test_gitlab_com_resolves_to_gitlab_forge_without_explicit_config(self, resolve):
        # gitlab.com is recognized even if api_credentials.gitlab.'gitlab.com'
        # is absent or its token env var is unset -- same "works unauthenticated"
        # fallback GitHub already had.
        forge = resolve("https://gitlab.com/petsc/petsc")
        assert isinstance(forge, GitLabForge)
        assert forge.host == "gitlab.com"

    def test_configured_self_hosted_instance_resolves_to_gitlab_forge(self, resolve):
        forge = resolve("https://gitlab.kitware.com/paraview/paraview")
        assert isinstance(forge, GitLabForge)
        assert forge.host == "gitlab.kitware.com"
        assert forge.api_base == "https://gitlab.kitware.com/api/v4"

    def test_unconfigured_self_hosted_instance_is_unrecognized(self, resolve):
        # Only hosts explicitly listed under api_credentials.gitlab (or
        # gitlab.com itself) are recognized -- an arbitrary self-hosted
        # GitLab this config doesn't know about must not be silently
        # treated as GitHub, matching the old gate's protective behavior.
        forge = resolve("https://gitlab.example.org/some/project")
        assert forge is None


class TestUnknownAndMissingHosts:
    def test_unrecognized_host_returns_none(self, resolve):
        assert resolve("https://bitbucket.org/owner/repo") is None

    def test_missing_repo_url_assumes_github(self, resolve):
        # Matches prepare_software_list's own fallback and existing tests
        # that build minimal package dicts without a repo_url.
        forge = resolve("")
        assert isinstance(forge, GitHubForge)


class TestRefExtractionRoundTrip:
    """Forge.for_repo picks the forge; the forge's own extract_ref must then
    actually parse the same repo_url it was resolved from -- a mismatch here
    (e.g. resolving GitLabForge for a URL its own extract_ref rejects) would
    silently skip every package on that host despite for_repo saying
    it's supported.
    """

    def test_github_ref_round_trips(self, resolve):
        url = "https://github.com/HDFGroup/hdf5"
        forge = resolve(url)
        assert forge.extract_ref(url) == "HDFGroup/hdf5"

    def test_gitlab_com_ref_round_trips(self, resolve):
        url = "https://gitlab.com/petsc/petsc"
        forge = resolve(url)
        assert forge.extract_ref(url) == "petsc/petsc"

    def test_self_hosted_ref_round_trips_including_nested_subgroups(self, resolve):
        url = "https://gitlab.kitware.com/vtk/vtk-m"
        forge = resolve(url)
        assert forge.extract_ref(url) == "vtk/vtk-m"


class TestExplicitRepoType:
    """package_config/<owner>_<repo>.yaml's repo_type overrides host inference."""

    def test_gitlab_type_recognizes_unlisted_self_hosted_host(self, resolve):
        forge = resolve("https://gitlab.example.org/g/p", "gitlab")
        assert isinstance(forge, GitLabForge)
        assert forge.api_base == "https://gitlab.example.org/api/v4"
        assert forge.extract_ref("https://gitlab.example.org/g/p") == "g/p"

    def test_gitlab_type_uses_listed_host_token(self):
        creds = {"gitlab": {"gitlab.kitware.com": {"token": "tok"}}}
        forge = Forge.for_repo("gitlab", "https://gitlab.kitware.com/vtk/vtk-m", creds)
        assert isinstance(forge, GitLabForge)
        assert forge.headers["PRIVATE-TOKEN"] == "tok"

    def test_github_token_comes_from_credentials(self):
        forge = Forge.for_repo("github", "https://github.com/o/r", {"github": {"token": "abc"}})
        assert forge.github_headers["Authorization"] == "token abc"

    def test_no_credentials_runs_unauthenticated(self):
        forge = Forge.for_repo("github", "https://github.com/o/r")
        assert "Authorization" not in forge.github_headers

    def test_github_type_on_github_com(self, resolve):
        forge = resolve("https://github.com/HDFGroup/hdf5", "github")
        assert isinstance(forge, GitHubForge)

    def test_github_type_on_other_host_is_refused(self, resolve):
        assert resolve("https://github.example.org/o/r", "github") is None

    def test_unknown_type_is_refused(self, resolve):
        assert resolve("https://github.com/o/r", "bitbucket") is None

    def test_gitlab_type_without_url_is_refused(self, resolve):
        assert resolve("", "gitlab") is None


class TestForgeForPackage:
    """The orchestrator passes the package file's repo_type, repo_url and
    its api_credentials straight to Forge.for_repo."""

    @pytest.fixture
    def orchestrator(self):
        return MetricsOrchestrator(config_path="config/orchestrator.yaml")

    def test_reads_repo_type_from_the_package(self, orchestrator):
        package = {"name": "p", "repo_type": "gitlab", "repo_url": "https://gitlab.example.org/g/p"}
        assert isinstance(orchestrator._forge_for_package(package), GitLabForge)

    def test_github_package(self, orchestrator):
        package = {"name": "hdf5", "repo_type": "github", "repo_url": "https://github.com/HDFGroup/hdf5"}
        assert isinstance(orchestrator._forge_for_package(package), GitHubForge)

    def test_without_repo_type_falls_back_to_host_inference(self, orchestrator):
        package = {"name": "p", "repo_url": "https://gitlab.example.org/g/p"}
        assert orchestrator._forge_for_package(package) is None


class TestSanitizeRepoType:
    def test_normalized_to_lowercase(self):
        from orchestrator import _sanitize_package_config
        assert _sanitize_package_config({"repo_type": " GitLab "})["repo_type"] == "gitlab"

    @pytest.mark.parametrize("bad", [None, "", 3, ["gitlab"]])
    def test_bad_values_dropped(self, bad):
        from orchestrator import _sanitize_package_config
        assert "repo_type" not in _sanitize_package_config({"repo_type": bad})
