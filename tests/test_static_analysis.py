"""Unit tests for StaticAnalysisCollector."""

import asyncio
import pytest
from forge.base import COLLECTION_GAP
from collectors.quality.static_analysis import StaticAnalysisCollector


class FakeForge:
    """Minimal stand-in for GitHubForge/GitLabForge."""

    def __init__(self):
        self.file_exists_results = {}
        self.dir_listing_result = []
        self.file_content_results = {}

    def extract_ref(self, repo_url):
        return None if repo_url == "not-a-url" else "owner/repo"

    async def file_exists(self, client, ref, path):
        return self.file_exists_results.get(path)

    async def dir_listing(self, client, ref, path):
        return self.dir_listing_result

    async def file_content(self, client, ref, path):
        return self.file_content_results.get(path)

    def get_timestamp(self):
        return "2026-01-01T00:00:00+00:00"


@pytest.fixture
def forge():
    return FakeForge()


@pytest.fixture
def collector(forge):
    return StaticAnalysisCollector(forge)


class TestEmptyResult:
    def test_structure(self, collector):
        result = collector._empty_result("MyPkg")
        assert result["package_name"] == "MyPkg"
        assert result["has_codeql"] is False
        assert result["workflow_file"] is None


class TestCollect:
    def test_collect_invalid_url(self, collector):
        result = asyncio.run(
            collector.collect({"name": "Bad", "repo_url": "not-a-url"})
        )
        assert result["has_codeql"] is False
        assert result["repository"] == "unknown"

    def test_finds_codeql_workflow(self, collector, forge):
        forge.file_exists_results = {
            ".github/workflows/codeql.yml": "https://github.com/owner/repo/blob/main/.github/workflows/codeql.yml",
        }
        result = asyncio.run(
            collector.collect({"name": "MyPkg", "repo_url": "https://github.com/owner/repo"})
        )
        assert result["has_codeql"] is True
        assert result["workflow_file"] == ".github/workflows/codeql.yml"
        assert result["workflow_url"]

    def test_no_codeql_workflow_found(self, collector, forge):
        forge.dir_listing_result = []
        result = asyncio.run(
            collector.collect({"name": "MyPkg", "repo_url": "https://github.com/owner/repo"})
        )
        assert result["has_codeql"] is False
        assert result["workflow_file"] is None
        assert "not_collected" not in result

    def test_filename_probe_gap_with_no_find_is_not_collected(self, collector, forge):
        forge.file_exists_results = {p: COLLECTION_GAP for p in [
            ".github/workflows/codeql.yml", ".github/workflows/codeql.yaml",
            ".github/workflows/codeql-analysis.yml", ".github/workflows/codeql-analysis.yaml",
        ]}
        forge.dir_listing_result = []
        result = asyncio.run(
            collector.collect({"name": "MyPkg", "repo_url": "https://github.com/owner/repo"})
        )
        assert result["has_codeql"] is False
        assert result["not_collected"] is True

    def test_found_workflow_survives_a_directory_listing_gap_elsewhere(self, collector, forge):
        # The direct filename probes find it before the fallback scan (which
        # would have gapped) is even reached.
        forge.file_exists_results = {
            ".github/workflows/codeql.yml": "https://github.com/owner/repo/blob/main/.github/workflows/codeql.yml",
        }
        forge.dir_listing_result = COLLECTION_GAP

        result = asyncio.run(
            collector.collect({"name": "MyPkg", "repo_url": "https://github.com/owner/repo"})
        )
        assert result["has_codeql"] is True
        assert "not_collected" not in result


class TestScanWorkflowsForCodeqlGapHandling:
    def test_directory_listing_gap_is_tracked(self, collector, forge):
        forge.dir_listing_result = COLLECTION_GAP
        found, saw_gap = asyncio.run(collector._scan_workflows_for_codeql(None, "o/r"))
        assert found is None
        assert saw_gap is True

    def test_confirmed_no_workflows_dir_is_not_a_gap(self, collector, forge):
        forge.dir_listing_result = []
        found, saw_gap = asyncio.run(collector._scan_workflows_for_codeql(None, "o/r"))
        assert found is None
        assert saw_gap is False

    def test_content_fetch_failure_on_a_candidate_is_a_gap(self, collector, forge):
        forge.dir_listing_result = [
            {"name": "ci.yml", "path": ".github/workflows/ci.yml", "html_url": "http://h"},
        ]
        forge.file_content_results = {".github/workflows/ci.yml": COLLECTION_GAP}
        found, saw_gap = asyncio.run(collector._scan_workflows_for_codeql(None, "o/r"))
        assert found is None
        assert saw_gap is True

    def test_match_found_despite_a_gap_on_another_candidate(self, collector, forge):
        forge.dir_listing_result = [
            {"name": "broken.yml", "path": ".github/workflows/broken.yml", "html_url": "http://b"},
            {"name": "codeql.yml", "path": ".github/workflows/codeql.yml", "html_url": "http://c"},
        ]
        forge.file_content_results = {
            ".github/workflows/broken.yml": COLLECTION_GAP,
            ".github/workflows/codeql.yml": "uses: github/codeql-action/analyze@v2",
        }
        found, saw_gap = asyncio.run(collector._scan_workflows_for_codeql(None, "o/r"))
        assert found is not None
        assert found["file"].endswith("codeql.yml")
