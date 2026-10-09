"""Unit tests for StaticAnalysisCollector."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from forge.base import COLLECTION_GAP
from collectors.quality.static_analysis import StaticAnalysisCollector
from tests.fakes import FakeForge, gitlab_fake

_URL = "https://github.com/owner/repo"
_CODEQL_PROBES = [
    ".github/workflows/codeql.yml", ".github/workflows/codeql.yaml",
    ".github/workflows/codeql-analysis.yml", ".github/workflows/codeql-analysis.yaml",
]


@pytest.fixture
def forge():
    return FakeForge()


@pytest.fixture
def collector(forge):
    return StaticAnalysisCollector(forge)


def _collect(collector):
    return asyncio.run(collector.collect({"name": "MyPkg", "repo_url": _URL}))


class TestEmptyResult:
    def test_structure(self, collector):
        result = collector._empty_result("MyPkg")
        assert result["package_name"] == "MyPkg"
        assert result["has_codeql"] is False
        assert result["workflow_file"] is None


class TestCollect:
    def test_collect_invalid_url(self, collector):
        result = asyncio.run(collector.collect({"name": "Bad", "repo_url": "not-a-url"}))
        assert result["has_codeql"] is False
        assert result["repository"] == "unknown"

    def test_finds_codeql_workflow(self, collector, forge):
        forge.files = {".github/workflows/codeql.yml": "name: CodeQL"}
        result = _collect(collector)
        assert result["has_codeql"] is True
        assert result["scanner"] == "CodeQL"
        assert result["workflow_file"] == ".github/workflows/codeql.yml"
        assert result["workflow_url"]

    def test_no_codeql_workflow_found(self, collector):
        result = _collect(collector)
        assert result["has_codeql"] is False
        assert result["workflow_file"] is None
        assert result["scanners_checked"] == "CodeQL"
        assert "not_collected" not in result

    def test_filename_probe_gap_with_no_find_is_not_collected(self, collector, forge):
        forge.gap_paths = set(_CODEQL_PROBES)
        result = _collect(collector)
        assert result["has_codeql"] is False
        assert result["not_collected"] is True

    def test_found_workflow_survives_a_listing_gap_elsewhere(self, collector, forge):
        # The direct filename probes find it before the fallback scan (which
        # would have gapped) is even reached.
        forge.files = {".github/workflows/codeql.yml": "name: CodeQL"}
        forge.gaps = {"ci_config_files", "ci_workflows"}
        result = _collect(collector)
        assert result["has_codeql"] is True
        assert "not_collected" not in result

    def test_codeql_bundled_in_another_workflow_is_found_by_content(self, collector, forge):
        # ADIOS2 runs CodeQL from everything.yml.
        forge.files = {".github/workflows/everything.yml": "- uses: github/codeql-action/init@v3"}
        result = _collect(collector)
        assert result["has_codeql"] is True
        assert result["workflow_file"] == ".github/workflows/everything.yml"


class TestFindDefaultSetup:
    def _run(self, collector, workflows):
        collector.forge.workflows = workflows
        return asyncio.run(collector._find_default_setup(None, "o/r"))

    def test_active_default_setup_found(self, collector):
        url, gap = self._run(collector, [{
            "path": "dynamic/github-code-scanning/codeql", "state": "active",
            "html_url": "https://github.com/o/r/actions/workflows/github-code-scanning/codeql",
        }])
        assert url == "https://github.com/o/r/actions/workflows/github-code-scanning/codeql"
        assert gap is False

    def test_disabled_default_setup_not_counted(self, collector):
        assert self._run(collector, [
            {"path": "dynamic/github-code-scanning/codeql", "state": "disabled_manually"}
        ]) == (None, False)

    def test_other_dynamic_workflows_not_counted(self, collector):
        assert self._run(collector, [
            {"path": "dynamic/dependabot/dependabot-updates", "state": "active"}
        ]) == (None, False)

    def test_gap_is_tracked(self, collector):
        collector.forge.gaps = {"ci_workflows"}
        assert asyncio.run(collector._find_default_setup(None, "o/r")) == (None, True)

    def test_collect_reports_default_setup(self, collector, monkeypatch):
        monkeypatch.setattr(collector, "_find_default_setup", AsyncMock(return_value=("https://x", False)))
        result = _collect(collector)
        assert result["has_codeql"] is True
        assert result["workflow_file"] == "CodeQL default setup"
        assert result["workflow_url"] == "https://x"


class TestScanWorkflowsForCodeqlGapHandling:
    def test_listing_gap_is_tracked(self, collector, forge):
        forge.gaps = {"ci_config_files"}
        found, saw_gap = asyncio.run(collector._scan_workflows_for_codeql(None, "o/r"))
        assert found is None
        assert saw_gap is True

    def test_confirmed_no_workflows_is_not_a_gap(self, collector):
        found, saw_gap = asyncio.run(collector._scan_workflows_for_codeql(None, "o/r"))
        assert found is None
        assert saw_gap is False

    def test_read_failure_on_a_candidate_is_a_gap(self, collector, forge):
        forge.files = {".github/workflows/ci.yml": "x"}
        forge.gap_paths = {".github/workflows/ci.yml"}
        found, saw_gap = asyncio.run(collector._scan_workflows_for_codeql(None, "o/r"))
        assert found is None
        assert saw_gap is True

    def test_match_found_despite_a_gap_on_another_candidate(self, collector, forge):
        forge.files = {
            ".github/workflows/broken.yml": "x",
            ".github/workflows/codeql.yml": "uses: github/codeql-action/analyze@v2",
        }
        forge.gap_paths = {".github/workflows/broken.yml"}
        found, saw_gap = asyncio.run(collector._scan_workflows_for_codeql(None, "o/r"))
        assert found is not None
        assert found["file"].endswith("codeql.yml")


class TestGitLab:
    def _collect(self, ci_text):
        forge = gitlab_fake(files={".gitlab-ci.yml": ci_text}, ci_files=[
            {"name": ".gitlab-ci.yml", "path": ".gitlab-ci.yml", "html_url": "u", "primary": True},
        ])
        return asyncio.run(StaticAnalysisCollector(forge).collect(
            {"name": "p", "repo_url": "https://gitlab.example.com/g/p"}))

    def test_sast_template_include_counts(self):
        result = self._collect("include:\n  - template: Jobs/SAST.gitlab-ci.yml\n")
        assert result["has_codeql"] is True
        assert result["scanner"] == "GitLab SAST"

    def test_no_scanner_names_both_options(self):
        result = self._collect("build:\n  script: make\n")
        assert result["has_codeql"] is False
        assert result["scanners_checked"] == "CodeQL or GitLab SAST"
