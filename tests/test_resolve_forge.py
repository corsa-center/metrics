"""Unit tests for MetricsOrchestrator._resolve_forge.

This replaced _is_known_non_github_repo (which only ever returned a bool
gating collection on/off) with something that actually picks a Forge --
GitHubForge for github.com, GitLabForge for gitlab.com or an explicitly
configured self-hosted instance, None (still skip) for anything else.
"""

import pytest

from orchestrator import MetricsOrchestrator
from forge.github import GitHubForge
from forge.gitlab import GitLabForge


@pytest.fixture
def orchestrator():
    return MetricsOrchestrator(config_path="config/orchestrator.yaml")


class TestKnownHosts:
    def test_github_com_resolves_to_github_forge(self, orchestrator):
        forge = orchestrator._resolve_forge("https://github.com/HDFGroup/hdf5")
        assert isinstance(forge, GitHubForge)

    def test_gitlab_com_resolves_to_gitlab_forge_without_explicit_config(self, orchestrator):
        # gitlab.com is recognized even if api_credentials.gitlab.'gitlab.com'
        # is absent or its token env var is unset -- same "works unauthenticated"
        # fallback GitHub already had.
        forge = orchestrator._resolve_forge("https://gitlab.com/petsc/petsc")
        assert isinstance(forge, GitLabForge)
        assert forge.host == "gitlab.com"

    def test_configured_self_hosted_instance_resolves_to_gitlab_forge(self, orchestrator):
        forge = orchestrator._resolve_forge("https://gitlab.kitware.com/paraview/paraview")
        assert isinstance(forge, GitLabForge)
        assert forge.host == "gitlab.kitware.com"
        assert forge.api_base == "https://gitlab.kitware.com/api/v4"

    def test_unconfigured_self_hosted_instance_is_unrecognized(self, orchestrator):
        # Only hosts explicitly listed under api_credentials.gitlab (or
        # gitlab.com itself) are recognized -- an arbitrary self-hosted
        # GitLab this config doesn't know about must not be silently
        # treated as GitHub, matching the old gate's protective behavior.
        forge = orchestrator._resolve_forge("https://gitlab.example.org/some/project")
        assert forge is None


class TestUnknownAndMissingHosts:
    def test_unrecognized_host_returns_none(self, orchestrator):
        assert orchestrator._resolve_forge("https://bitbucket.org/owner/repo") is None

    def test_missing_repo_url_assumes_github(self, orchestrator):
        # Matches prepare_software_list's own fallback and existing tests
        # that build minimal package dicts without a repo_url.
        forge = orchestrator._resolve_forge("")
        assert isinstance(forge, GitHubForge)


class TestRefExtractionRoundTrip:
    """_resolve_forge picks the forge; the forge's own extract_ref must then
    actually parse the same repo_url it was resolved from -- a mismatch here
    (e.g. resolving GitLabForge for a URL its own extract_ref rejects) would
    silently skip every package on that host despite _resolve_forge saying
    it's supported.
    """

    def test_github_ref_round_trips(self, orchestrator):
        url = "https://github.com/HDFGroup/hdf5"
        forge = orchestrator._resolve_forge(url)
        assert forge.extract_ref(url) == "HDFGroup/hdf5"

    def test_gitlab_com_ref_round_trips(self, orchestrator):
        url = "https://gitlab.com/petsc/petsc"
        forge = orchestrator._resolve_forge(url)
        assert forge.extract_ref(url) == "petsc/petsc"

    def test_self_hosted_ref_round_trips_including_nested_subgroups(self, orchestrator):
        url = "https://gitlab.kitware.com/vtk/vtk-m"
        forge = orchestrator._resolve_forge(url)
        assert forge.extract_ref(url) == "vtk/vtk-m"


class TestExplicitRepoType:
    """package_config/<owner>_<repo>.yaml's repo_type overrides host inference."""

    def test_gitlab_type_recognizes_unlisted_self_hosted_host(self, orchestrator):
        forge = orchestrator._resolve_forge("https://gitlab.example.org/g/p", "gitlab")
        assert isinstance(forge, GitLabForge)
        assert forge.api_base == "https://gitlab.example.org/api/v4"
        assert forge.extract_ref("https://gitlab.example.org/g/p") == "g/p"

    def test_gitlab_type_uses_listed_host_token(self, orchestrator):
        orchestrator.config.setdefault("api_credentials", {}).setdefault("gitlab", {})[
            "gitlab.kitware.com"
        ] = {"token": "tok"}
        forge = orchestrator._resolve_forge("https://gitlab.kitware.com/vtk/vtk-m", "gitlab")
        assert isinstance(forge, GitLabForge)
        assert forge.headers["PRIVATE-TOKEN"] == "tok"

    def test_github_type_on_github_com(self, orchestrator):
        forge = orchestrator._resolve_forge("https://github.com/HDFGroup/hdf5", "github")
        assert isinstance(forge, GitHubForge)

    def test_github_type_on_other_host_is_refused(self, orchestrator):
        assert orchestrator._resolve_forge("https://github.example.org/o/r", "github") is None

    def test_unknown_type_is_refused(self, orchestrator):
        assert orchestrator._resolve_forge("https://github.com/o/r", "bitbucket") is None

    def test_gitlab_type_without_url_is_refused(self, orchestrator):
        assert orchestrator._resolve_forge("", "gitlab") is None


class TestForgeForPackage:
    def test_reads_repo_type_from_attached_package_config(self, orchestrator):
        package = {
            "repository": "g/p",
            "repo_url": "https://gitlab.example.org/g/p",
            "package_config": {"repo_type": "gitlab"},
        }
        assert isinstance(orchestrator._forge_for_package(package), GitLabForge)

    def test_without_repo_type_falls_back_to_host_inference(self, orchestrator):
        package = {
            "repository": "g/p",
            "repo_url": "https://gitlab.example.org/g/p",
            "package_config": {},
        }
        assert orchestrator._forge_for_package(package) is None


class TestSanitizeRepoType:
    def test_normalized_to_lowercase(self):
        from orchestrator import _sanitize_metric_config
        assert _sanitize_metric_config({"repo_type": " GitLab "})["repo_type"] == "gitlab"

    @pytest.mark.parametrize("bad", [None, "", 3, ["gitlab"]])
    def test_bad_values_dropped(self, bad):
        from orchestrator import _sanitize_metric_config
        assert "repo_type" not in _sanitize_metric_config({"repo_type": bad})
