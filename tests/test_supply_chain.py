"""Unit tests for SupplyChainCollector (CASS Section 4.3.8)."""

import asyncio
import base64
import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from collectors.ecosystem.base import COLLECTION_GAP, RepoTree
from collectors.quality.supply_chain import (
    SupplyChainCollector, _parse_pinned_pypi_deps, _parse_uv_lock,
    _parse_poetry_lock, _parse_cargo_lock, _parse_go_sum, _parse_pipfile_lock,
)


@pytest.fixture
def collector():
    return SupplyChainCollector()


class TestEmptyResult:
    def test_structure(self, collector):
        result = collector._empty_result("MyPkg")
        assert result["package_name"] == "MyPkg"
        assert result["has_sbom"] is False
        assert result["has_build_provenance"] is False
        assert result["overall_score"]["max_score"] == 2
        assert result["sub_metrics"]["dependency_vulnerability_posture"]["not_collected"] is True
        assert result["sub_metrics"]["dependency_freshness"]["not_collected"] is True


class TestCollectInvalidUrl:
    def test_invalid_url_returns_empty(self, collector):
        result = asyncio.run(
            collector.collect({"name": "Bad", "repo_url": "not-a-url"})
        )
        assert result["has_sbom"] is False
        assert result["repository"] == "unknown"


class TestFindSbom:
    def test_root_file_wins(self, collector):
        result = collector._find_sbom("https://github.com/o/r/blob/main/sbom.spdx.json", [])
        assert result["passing"] is True
        assert result["value"] == "Committed to repository"

    def test_release_asset_match(self, collector):
        assets = [{"name": "app-v1.0.0.sbom.spdx.json", "url": "http://x", "release": "v1.0.0"}]
        result = collector._find_sbom(None, assets)
        assert result["passing"] is True
        assert "v1.0.0" in result["value"]

    def test_cyclonedx_asset_match(self, collector):
        assets = [{"name": "bom.cdx.json", "url": "http://x", "release": "v2.0.0"}]
        result = collector._find_sbom(None, assets)
        assert result["passing"] is True

    def test_no_match(self, collector):
        assets = [{"name": "checksums.txt", "url": "http://x", "release": "v1.0.0"}]
        result = collector._find_sbom(None, assets)
        assert result["passing"] is False
        assert result["value"] == "No SBOM found"

    def test_unrelated_filename_is_not_a_false_positive(self, collector):
        # "bom.xml" hint shouldn't match an unrelated file like "abomination.txt"
        assets = [{"name": "abomination.txt", "url": "http://x", "release": "v1"}]
        result = collector._find_sbom(None, assets)
        assert result["passing"] is False

    def test_no_match_under_gap_is_not_collected_not_a_negative(self, collector):
        result = collector._find_sbom(None, [], has_gap=True)
        assert result["passing"] is False
        assert result["not_collected"] is True

    def test_positive_match_survives_a_gap_elsewhere(self, collector):
        # Found in a release asset despite has_gap=True (e.g. the root
        # check gapped but the release-assets fetch succeeded) -- still real.
        assets = [{"name": "sbom.spdx.json", "url": "http://x", "release": "v1"}]
        result = collector._find_sbom(None, assets, has_gap=True)
        assert result["passing"] is True
        assert "not_collected" not in result


class TestFindProvenance:
    def test_intoto_attestation_match(self, collector):
        assets = [{"name": "multiple.intoto.jsonl", "url": "http://x", "release": "v1.0.0"}]
        result = collector._find_provenance(assets)
        assert result["passing"] is True

    def test_slsa_match(self, collector):
        assets = [{"name": "slsa-provenance.json", "url": "http://x", "release": "v1.0.0"}]
        result = collector._find_provenance(assets)
        assert result["passing"] is True

    def test_no_match(self, collector):
        assets = [{"name": "release.tar.gz", "url": "http://x", "release": "v1.0.0"}]
        result = collector._find_provenance(assets)
        assert result["passing"] is False
        assert result["value"] == "No build provenance found"

    def test_no_match_under_gap_is_not_collected_not_a_negative(self, collector):
        result = collector._find_provenance([], has_gap=True)
        assert result["passing"] is False
        assert result["not_collected"] is True


class TestComputeOverall:
    def test_both_passing(self, collector):
        sub = {
            "sbom_detection": {"passing": True},
            "build_provenance": {"passing": True},
            "dependency_vulnerability_posture": {"passing": False, "not_collected": True},
            "dependency_freshness": {"passing": False, "not_collected": True},
        }
        overall = collector._compute_overall(sub)
        assert overall == {"score": 2, "max_score": 2, "percentage": 100.0}

    def test_not_collected_excluded_from_max_score(self, collector):
        sub = {
            "sbom_detection": {"passing": False},
            "build_provenance": {"passing": False},
            "dependency_vulnerability_posture": {"passing": False, "not_collected": True},
            "dependency_freshness": {"passing": False, "not_collected": True},
        }
        overall = collector._compute_overall(sub)
        assert overall["max_score"] == 2
        assert overall["score"] == 0

    def test_all_not_collected_gives_zero_not_error(self, collector):
        sub = {"x": {"passing": False, "not_collected": True}}
        overall = collector._compute_overall(sub)
        assert overall == {"score": 0, "max_score": 0, "percentage": 0.0}


class TestCheckRootSbom:
    """_check_root_sbom now takes a RepoTree (or COLLECTION_GAP) directly --
    see METRIC_BLIND_SPOTS.md class F1.
    """

    def test_found(self, collector):
        tree = RepoTree("o", "r", ["sbom.spdx.json"], truncated=False)
        url, saw_gap = collector._check_root_sbom(tree)
        assert url is not None
        assert saw_gap is False

    def test_not_found(self, collector):
        tree = RepoTree("o", "r", ["README.md"], truncated=False)
        url, saw_gap = collector._check_root_sbom(tree)
        assert url is None
        assert saw_gap is False

    def test_gapped_tree_is_tracked(self, collector):
        url, saw_gap = collector._check_root_sbom(COLLECTION_GAP)
        assert url is None
        assert saw_gap is True

    def test_match_is_case_insensitive(self, collector):
        tree = RepoTree("o", "r", ["SBOM.SPDX.JSON"], truncated=False)
        url, saw_gap = collector._check_root_sbom(tree)
        assert url is not None


class TestFetchReleaseAssets:
    def _mock_client(self, status_code, body=None):
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        mock_resp.json.return_value = body
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        return mock_client

    def test_flattens_assets_across_releases(self, collector):
        releases = [
            {"tag_name": "v2.0.0", "assets": [{"name": "a.tar.gz", "browser_download_url": "u1"}]},
            {"tag_name": "v1.0.0", "assets": [{"name": "sbom.json", "browser_download_url": "u2"}]},
        ]
        client = self._mock_client(200, releases)
        assets, is_gap = asyncio.run(collector._fetch_release_assets(client, "o", "r"))
        assert len(assets) == 2
        assert assets[1]["release"] == "v1.0.0"
        assert is_gap is False

    def test_no_assets(self, collector):
        client = self._mock_client(200, [{"tag_name": "v1.0.0", "assets": []}])
        assets, is_gap = asyncio.run(collector._fetch_release_assets(client, "o", "r"))
        assert assets == []
        assert is_gap is False

    def test_confirmed_404_is_a_real_empty_list_not_a_gap(self, collector):
        # A repo with no releases at all -- a real, trustworthy result.
        client = self._mock_client(404)
        assets, is_gap = asyncio.run(collector._fetch_release_assets(client, "o", "r"))
        assert assets == []
        assert is_gap is False

    def test_request_failure_is_a_gap_not_a_confirmed_empty_list(self, collector):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("boom"))
        assets, is_gap = asyncio.run(collector._fetch_release_assets(mock_client, "o", "r"))
        assert assets == []
        assert is_gap is True

    def test_rate_limited_after_retries_is_a_gap(self, collector):
        client = self._mock_client(403)
        assets, is_gap = asyncio.run(collector._fetch_release_assets(client, "o", "r"))
        assert assets == []
        assert is_gap is True


class TestParsePinnedPypiDeps:
    """OSV.dev's query API takes a single version, not a range -- only an
    exact == pin is a checkable dependency.
    """

    def test_exact_pin_parsed(self):
        assert _parse_pinned_pypi_deps("numpy==1.24.0") == [("numpy", "1.24.0")]

    def test_range_specifier_skipped(self):
        assert _parse_pinned_pypi_deps("numpy>=1.20") == []

    def test_bare_name_skipped(self):
        assert _parse_pinned_pypi_deps("numpy") == []

    def test_comment_and_blank_lines_ignored(self):
        text = "# a comment\n\nnumpy==1.24.0\n"
        assert _parse_pinned_pypi_deps(text) == [("numpy", "1.24.0")]

    def test_pip_option_lines_skipped(self):
        text = "-e .\n-r base.txt\n--index-url https://example.com\nnumpy==1.24.0"
        assert _parse_pinned_pypi_deps(text) == [("numpy", "1.24.0")]

    def test_extras_bracket_stripped(self):
        assert _parse_pinned_pypi_deps("requests[security]==2.31.0") == [
            ("requests", "2.31.0")
        ]

    def test_environment_marker_stripped(self):
        text = 'numpy==1.24.0; python_version >= "3.8"'
        assert _parse_pinned_pypi_deps(text) == [("numpy", "1.24.0")]

    def test_inline_comment_stripped(self):
        assert _parse_pinned_pypi_deps("numpy==1.24.0  # pinned for CI") == [
            ("numpy", "1.24.0")
        ]

    def test_vcs_requirement_skipped(self):
        text = "git+https://github.com/foo/bar.git@v1.0#egg=bar"
        assert _parse_pinned_pypi_deps(text) == []

    def test_multiple_lines(self):
        text = "numpy==1.24.0\nscipy>=1.10\npandas==2.0.1\n"
        assert _parse_pinned_pypi_deps(text) == [
            ("numpy", "1.24.0"), ("pandas", "2.0.1"),
        ]


class TestQueryOsvBatch:
    def _client(self, status_code, body=None, side_effect=None):
        client = AsyncMock()
        if side_effect:
            client.post = AsyncMock(side_effect=side_effect)
        else:
            resp = MagicMock()
            resp.status_code = status_code
            resp.json.return_value = body or {}
            client.post = AsyncMock(return_value=resp)
        return client

    def test_no_vulnerabilities_returns_empty(self, collector):
        client = self._client(200, {"results": [{}]})
        result = asyncio.run(collector._query_osv_batch(client, [("PyPI", "numpy", "1.24.0")]))
        assert result == []

    def test_vulnerable_dependency_returned(self, collector):
        client = self._client(200, {"results": [
            {"vulns": [{"id": "GHSA-xxxx"}]},
            {},
        ]})
        deps = [("PyPI", "requests", "2.6.0"), ("PyPI", "numpy", "1.24.0")]
        result = asyncio.run(collector._query_osv_batch(client, deps))
        assert result == [("PyPI", "requests", "2.6.0")]

    def test_multiple_ecosystems_in_one_batch(self, collector):
        client = self._client(200, {"results": [{}, {"vulns": [{"id": "GHSA-yyyy"}]}]})
        deps = [("PyPI", "numpy", "1.24.0"), ("crates.io", "serde", "1.0.0")]
        result = asyncio.run(collector._query_osv_batch(client, deps))
        assert result == [("crates.io", "serde", "1.0.0")]

    def test_non_200_is_a_gap(self, collector):
        client = self._client(500)
        result = asyncio.run(collector._query_osv_batch(client, [("PyPI", "numpy", "1.24.0")]))
        assert result is COLLECTION_GAP

    def test_network_exception_is_a_gap(self, collector):
        client = self._client(None, side_effect=httpx.ConnectError("boom"))
        result = asyncio.run(collector._query_osv_batch(client, [("PyPI", "numpy", "1.24.0")]))
        assert result is COLLECTION_GAP

    def test_empty_deps_list(self, collector):
        client = self._client(200, {"results": []})
        result = asyncio.run(collector._query_osv_batch(client, []))
        assert result == []


class TestCheckDependencyVulnerabilities:
    def test_gapped_tree_is_not_collected(self, collector):
        result = asyncio.run(
            collector._check_dependency_vulnerabilities(None, "o", "r", COLLECTION_GAP)
        )
        assert result["not_collected"] is True

    def test_no_lockfile_passes(self, collector):
        tree = RepoTree("o", "r", ["README.md"], truncated=False)
        result = asyncio.run(
            collector._check_dependency_vulnerabilities(None, "o", "r", tree)
        )
        assert result["passing"] is True
        assert "No dependency lockfile" in result["value"]

    def test_nested_requirements_txt_not_matched(self, collector):
        # docs/requirements.txt is Sphinx tooling, not the project's own
        # dependency surface -- deliberately out of scope.
        tree = RepoTree("o", "r", ["docs/requirements.txt"], truncated=False)
        result = asyncio.run(
            collector._check_dependency_vulnerabilities(None, "o", "r", tree)
        )
        assert result["passing"] is True
        assert "No dependency lockfile" in result["value"]

    def test_clean_scan_passes(self, collector):
        tree = RepoTree("o", "r", ["requirements.txt"], truncated=False)
        content = base64.b64encode(b"numpy==1.24.0\n").decode()

        async def fake_get(client, url, params=None):
            return {"content": content}

        async def fake_batch(client, deps):
            return []

        with patch.object(collector, "_github_get", side_effect=fake_get), \
             patch.object(collector, "_query_osv_batch", side_effect=fake_batch):
            result = asyncio.run(
                collector._check_dependency_vulnerabilities(None, "o", "r", tree)
            )
        assert result["passing"] is True
        assert "0 of 1" in result["value"]

    def test_vulnerable_dependency_fails(self, collector):
        tree = RepoTree("o", "r", ["requirements.txt"], truncated=False)
        content = base64.b64encode(b"requests==2.6.0\n").decode()

        async def fake_get(client, url, params=None):
            return {"content": content}

        async def fake_batch(client, deps):
            return [("PyPI", "requests", "2.6.0")]

        with patch.object(collector, "_github_get", side_effect=fake_get), \
             patch.object(collector, "_query_osv_batch", side_effect=fake_batch):
            result = asyncio.run(
                collector._check_dependency_vulnerabilities(None, "o", "r", tree)
            )
        assert result["passing"] is False
        assert result["detail"] == ["requests==2.6.0 (PyPI)"]

    def test_no_pinned_deps_still_passes(self, collector):
        tree = RepoTree("o", "r", ["requirements.txt"], truncated=False)
        content = base64.b64encode(b"numpy>=1.20\nscipy\n").decode()

        async def fake_get(client, url, params=None):
            return {"content": content}

        with patch.object(collector, "_github_get", side_effect=fake_get):
            result = asyncio.run(
                collector._check_dependency_vulnerabilities(None, "o", "r", tree)
            )
        assert result["passing"] is True
        assert "no exactly-pinned/registry dependencies" in result["value"]

    def test_content_fetch_gap_is_not_collected(self, collector):
        tree = RepoTree("o", "r", ["requirements.txt"], truncated=False)

        async def fake_get(client, url, params=None):
            return COLLECTION_GAP

        with patch.object(collector, "_github_get", side_effect=fake_get):
            result = asyncio.run(
                collector._check_dependency_vulnerabilities(None, "o", "r", tree)
            )
        assert result["not_collected"] is True

    def test_osv_query_gap_is_not_collected(self, collector):
        tree = RepoTree("o", "r", ["requirements.txt"], truncated=False)
        content = base64.b64encode(b"numpy==1.24.0\n").decode()

        async def fake_get(client, url, params=None):
            return {"content": content}

        async def fake_batch(client, deps):
            return COLLECTION_GAP

        with patch.object(collector, "_github_get", side_effect=fake_get), \
             patch.object(collector, "_query_osv_batch", side_effect=fake_batch):
            result = asyncio.run(
                collector._check_dependency_vulnerabilities(None, "o", "r", tree)
            )
        assert result["not_collected"] is True

    def test_multiple_lockfiles_merged_into_one_query(self, collector):
        # A repo can pin dependencies more than one way -- Python bindings
        # via requirements.txt alongside a Rust component's Cargo.lock.
        tree = RepoTree("o", "r", ["requirements.txt", "Cargo.lock"], truncated=False)
        py_content = base64.b64encode(b"numpy==1.24.0\n").decode()
        cargo_content = base64.b64encode(
            b'[[package]]\nname = "serde"\nversion = "1.0.0"\n'
            b'source = "registry+https://github.com/rust-lang/crates.io-index"\n'
        ).decode()

        async def fake_get(client, url, params=None):
            if "requirements.txt" in url:
                return {"content": py_content}
            return {"content": cargo_content}

        seen = []

        async def fake_batch(client, deps):
            seen.extend(deps)
            return []

        with patch.object(collector, "_github_get", side_effect=fake_get), \
             patch.object(collector, "_query_osv_batch", side_effect=fake_batch):
            result = asyncio.run(
                collector._check_dependency_vulnerabilities(None, "o", "r", tree)
            )
        assert result["passing"] is True
        assert set(seen) == {("PyPI", "numpy", "1.24.0"), ("crates.io", "serde", "1.0.0")}

    def test_a_positive_finding_stands_despite_a_gap_on_another_lockfile(self, collector):
        tree = RepoTree("o", "r", ["requirements.txt", "Cargo.lock"], truncated=False)
        py_content = base64.b64encode(b"requests==2.6.0\n").decode()

        async def fake_get(client, url, params=None):
            if "requirements.txt" in url:
                return {"content": py_content}
            return COLLECTION_GAP  # Cargo.lock content fetch fails

        async def fake_batch(client, deps):
            return [("PyPI", "requests", "2.6.0")]

        with patch.object(collector, "_github_get", side_effect=fake_get), \
             patch.object(collector, "_query_osv_batch", side_effect=fake_batch):
            result = asyncio.run(
                collector._check_dependency_vulnerabilities(None, "o", "r", tree)
            )
        assert result["passing"] is False
        assert "not_collected" not in result

    def test_clean_result_with_a_gap_on_another_lockfile_is_not_collected(self, collector):
        tree = RepoTree("o", "r", ["requirements.txt", "Cargo.lock"], truncated=False)
        py_content = base64.b64encode(b"numpy==1.24.0\n").decode()

        async def fake_get(client, url, params=None):
            if "requirements.txt" in url:
                return {"content": py_content}
            return COLLECTION_GAP

        async def fake_batch(client, deps):
            return []

        with patch.object(collector, "_github_get", side_effect=fake_get), \
             patch.object(collector, "_query_osv_batch", side_effect=fake_batch):
            result = asyncio.run(
                collector._check_dependency_vulnerabilities(None, "o", "r", tree)
            )
        # A clean scan of requirements.txt alone can't stand in for the
        # Cargo.lock that couldn't be read -- it might have held the issue.
        assert result["not_collected"] is True


class TestParseUvLock:
    def test_registry_sourced_package_included(self):
        text = (
            '[[package]]\nname = "requests"\nversion = "2.31.0"\n'
            'source = { registry = "https://pypi.org/simple" }\n'
        )
        assert _parse_uv_lock(text) == [("requests", "2.31.0")]

    def test_path_sourced_package_excluded(self):
        text = (
            '[[package]]\nname = "mypkg"\nversion = "0.1.0"\n'
            'source = { editable = "." }\n'
        )
        assert _parse_uv_lock(text) == []

    def test_git_sourced_package_excluded(self):
        text = (
            '[[package]]\nname = "mypkg"\nversion = "0.1.0"\n'
            'source = { git = "https://github.com/foo/bar" }\n'
        )
        assert _parse_uv_lock(text) == []

    def test_invalid_toml_returns_empty(self):
        assert _parse_uv_lock("not valid toml {{{") == []

    def test_no_package_table_returns_empty(self):
        assert _parse_uv_lock("version = 1\n") == []


class TestParsePoetryLock:
    def test_default_source_is_a_registry_package(self):
        # poetry.lock only records [package.source] for git/url/directory/
        # file dependencies -- its ABSENCE means a normal PyPI package.
        text = '[[package]]\nname = "requests"\nversion = "2.31.0"\n'
        assert _parse_poetry_lock(text) == [("requests", "2.31.0")]

    def test_legacy_index_source_still_included(self):
        text = (
            '[[package]]\nname = "requests"\nversion = "2.31.0"\n'
            '[package.source]\ntype = "legacy"\nurl = "https://example.com/simple"\n'
        )
        assert _parse_poetry_lock(text) == [("requests", "2.31.0")]

    def test_git_source_excluded(self):
        text = (
            '[[package]]\nname = "mypkg"\nversion = "0.1.0"\n'
            '[package.source]\ntype = "git"\nurl = "https://github.com/foo/bar"\n'
        )
        assert _parse_poetry_lock(text) == []

    def test_directory_source_excluded(self):
        text = (
            '[[package]]\nname = "mypkg"\nversion = "0.1.0"\n'
            '[package.source]\ntype = "directory"\nurl = "./local"\n'
        )
        assert _parse_poetry_lock(text) == []

    def test_invalid_toml_returns_empty(self):
        assert _parse_poetry_lock("not valid toml {{{") == []


class TestParseCargoLock:
    def test_registry_sourced_package_included(self):
        text = (
            '[[package]]\nname = "serde"\nversion = "1.0.0"\n'
            'source = "registry+https://github.com/rust-lang/crates.io-index"\n'
        )
        assert _parse_cargo_lock(text) == [("serde", "1.0.0")]

    def test_workspace_crate_with_no_source_excluded(self):
        # The workspace's own crates have no source field at all.
        text = '[[package]]\nname = "my-local-crate"\nversion = "0.1.0"\n'
        assert _parse_cargo_lock(text) == []

    def test_git_sourced_package_excluded(self):
        text = (
            '[[package]]\nname = "mycrate"\nversion = "0.1.0"\n'
            'source = "git+https://github.com/foo/bar#abc123"\n'
        )
        assert _parse_cargo_lock(text) == []

    def test_invalid_toml_returns_empty(self):
        assert _parse_cargo_lock("not valid toml {{{") == []

    def test_multiple_packages(self):
        text = (
            '[[package]]\nname = "serde"\nversion = "1.0.0"\n'
            'source = "registry+https://github.com/rust-lang/crates.io-index"\n\n'
            '[[package]]\nname = "local-crate"\nversion = "0.1.0"\n'
        )
        assert _parse_cargo_lock(text) == [("serde", "1.0.0")]


class TestParseGoSum:
    def test_content_hash_line_included(self):
        text = "github.com/gin-gonic/gin v1.7.0 h1:jGZM8QwSXvipzXW4gAG8AGN0nfp/1QLZC/QVjyGH5Ys=\n"
        assert _parse_go_sum(text) == [("github.com/gin-gonic/gin", "v1.7.0")]

    def test_go_mod_line_excluded(self):
        # Each module also gets a "/go.mod h1:..." line -- a duplicate of
        # the same (module, version) pair that must not be double-counted.
        text = (
            "github.com/gin-gonic/gin v1.7.0 h1:jGZM8QwSXvipzXW4gAG8AGN0nfp/1QLZC/QVjyGH5Ys=\n"
            "github.com/gin-gonic/gin v1.7.0/go.mod h1:jD2toBW3GZUr5UMcdX21G4wIt0Zjuvcz/1BqRDwFdCU=\n"
        )
        assert _parse_go_sum(text) == [("github.com/gin-gonic/gin", "v1.7.0")]

    def test_duplicate_content_hash_lines_deduped(self):
        text = (
            "github.com/gin-gonic/gin v1.7.0 h1:aaa=\n"
            "github.com/gin-gonic/gin v1.7.0 h1:aaa=\n"
        )
        assert _parse_go_sum(text) == [("github.com/gin-gonic/gin", "v1.7.0")]

    def test_malformed_lines_skipped(self):
        assert _parse_go_sum("not a go.sum line at all\n") == []

    def test_multiple_modules(self):
        text = (
            "github.com/gin-gonic/gin v1.7.0 h1:aaa=\n"
            "github.com/gin-gonic/gin v1.7.0/go.mod h1:bbb=\n"
            "golang.org/x/net v0.0.0-20210226172049-e18ecbb05110 h1:ccc=\n"
        )
        assert _parse_go_sum(text) == [
            ("github.com/gin-gonic/gin", "v1.7.0"),
            ("golang.org/x/net", "v0.0.0-20210226172049-e18ecbb05110"),
        ]


class TestParsePipfileLock:
    def test_default_section_pinned_entry_included(self):
        text = '{"default": {"requests": {"version": "==2.31.0"}}}'
        assert _parse_pipfile_lock(text) == [("requests", "2.31.0")]

    def test_develop_section_included(self):
        # A dev/test-only dependency still runs in CI, so it's a real
        # finding, not noise -- unlike the root-only lockfile-location rule.
        text = '{"develop": {"pytest": {"version": "==7.4.0"}}}'
        assert _parse_pipfile_lock(text) == [("pytest", "7.4.0")]

    def test_both_sections_merged(self):
        text = (
            '{"default": {"requests": {"version": "==2.31.0"}}, '
            '"develop": {"pytest": {"version": "==7.4.0"}}}'
        )
        assert _parse_pipfile_lock(text) == [
            ("requests", "2.31.0"),
            ("pytest", "7.4.0"),
        ]

    def test_non_pinned_entry_excluded(self):
        text = '{"default": {"requests": {"version": "*"}}}'
        assert _parse_pipfile_lock(text) == []

    def test_vcs_entry_with_no_version_excluded(self):
        text = '{"default": {"mypkg": {"git": "https://github.com/foo/bar"}}}'
        assert _parse_pipfile_lock(text) == []

    def test_invalid_json_returns_empty(self):
        assert _parse_pipfile_lock("not valid json {{{") == []
