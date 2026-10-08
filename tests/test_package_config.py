"""Unit tests for per-package metric config: precedence in _sub_enabled,
_package_excluded_keys provenance, and fetching/validating a packages own
package configuration file.
"""

import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator import MetricsOrchestrator, _sanitize_package_config


def _orch(config=None):
    o = MetricsOrchestrator.__new__(MetricsOrchestrator)
    o.config = config or {}
    o.ecosystem_collectors = (config or {}).get("ecosystem_collectors", {})
    o.quality_collectors = (config or {}).get("quality_collectors", {})
    return o


class TestSubEnabledPrecedence:
    def test_package_config_narrows_global_default(self):
        o = _orch()
        package = {"collectors": {"ecosystem": {"funding": False}}}
        assert o._sub_enabled("ecosystem", "funding", package) is False
        assert o._sub_enabled("ecosystem", "licensing", package) is True

    def test_global_off_wins_over_package_attempting_to_enable(self):
        o = _orch({"ecosystem_collectors": {"licensing": False}})
        package = {"collectors": {"ecosystem": {"licensing": True}}}
        assert o._sub_enabled("ecosystem", "licensing", package) is False

    def test_no_package_context_behaves_like_global_only(self):
        o = _orch({"ecosystem_collectors": {"licensing": False}})
        assert o._sub_enabled("ecosystem", "licensing") is False
        assert o._sub_enabled("ecosystem", "engagement") is True

    def test_groups_are_independent_under_package_config(self):
        o = _orch()
        package = {"collectors": {"ecosystem": {"licensing": False}}}
        assert o._sub_enabled("ecosystem", "licensing", package) is False
        assert o._sub_enabled("quality", "licensing", package) is True

    def test_absent_package_config_defaults_to_enabled(self):
        o = _orch()
        assert o._sub_enabled("ecosystem", "funding", {}) is True


class TestPackageExcludedKeys:
    def test_reports_only_package_level_exclusions(self):
        o = _orch({"ecosystem_collectors": {"engagement": False}})
        package = {"collectors": {"ecosystem": {"funding": False}}}
        excluded = o._package_excluded_keys("ecosystem", package)
        assert excluded == ["funding"]
        assert "engagement" not in excluded  # globally off, not a package exclusion

    def test_no_exclusions_returns_empty_list(self):
        o = _orch()
        assert o._package_excluded_keys("quality", {}) == []

    def test_typo_d_key_has_no_effect_and_is_not_reported(self):
        # "supplly_chain" isn't a real collector -- _sub_enabled would never
        # look it up, so it must not show up as a deliberate exclusion.
        o = _orch()
        package = {"collectors": {"quality": {"supplly_chain": False}}}
        assert o._package_excluded_keys("quality", package) == []
        # And the real collector it was probably meant to name is unaffected.
        assert o._sub_enabled("quality", "supply_chain", package) is True


class TestSanitizePackageConfig:
    def test_passes_through_well_formed_config(self):
        data = {
            "schema": 1,
            "name": "package_name",
            "repo_url": "package_repo_url",
            "collectors": {"quality": {"supply_chain": False}},
            "overrides": {"4.2.8": {"NIH R50 Award Tracking": "N/A"}},
        }
        assert _sanitize_package_config(data) == data

    def test_null_collectors_block_is_dropped_not_raised(self):
        result = _sanitize_package_config({"schema": 1, "collectors": None})
        assert "collectors" not in result

    def test_null_overrides_block_is_dropped_not_raised(self):
        result = _sanitize_package_config({"schema": 1, "overrides": None})
        assert "overrides" not in result

    def test_wrong_type_group_value_is_dropped(self):
        # collectors.quality should be a dict; a string here is a plausible
        # hand-edit mistake and must not survive to be dict-chained later.
        result = _sanitize_package_config({"collectors": {"quality": "oops"}})
        assert result.get("collectors", {}) == {}

    def test_wrong_type_override_section_is_dropped(self):
        result = _sanitize_package_config({"overrides": {"4.2.8": "N/A"}})
        assert result.get("overrides", {}) == {}

    def test_non_dict_input_returns_empty(self):
        assert _sanitize_package_config(None) == {}
        assert _sanitize_package_config("not a mapping") == {}


def _mock_async_client(mock_resp):
    """A context-manager mock standing in for `async with httpx.AsyncClient() as client`."""
    client = AsyncMock()
    client.get = AsyncMock(return_value=mock_resp)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _mock_response(status_code=200, content_b64=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {"content": content_b64} if content_b64 else {}
    return resp


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


_HDF5_YAML = """
schema: 1
name: HDF5
repo_type: github
repo_url: https://github.com/HDFGroup/hdf5
overrides:
  "4.2.8":
    NIH R50 Award Tracking: N/A
"""


class TestFetchPackageConfig:
    def _fetch(self, resp):
        o = _orch()
        with patch("orchestrator.httpx.AsyncClient", return_value=_mock_async_client(resp)):
            return asyncio.run(o._fetch_package_config("https://api.github.com/x"))

    def test_valid_package_is_returned(self):
        package = self._fetch(_mock_response(content_b64=_b64(_HDF5_YAML)))
        assert package["name"] == "HDF5"
        assert package["overrides"] == {"4.2.8": {"NIH R50 Award Tracking": "N/A"}}

    def test_http_error_returns_empty(self):
        assert self._fetch(_mock_response(status_code=404)) == {}

    def test_wrong_schema_returns_empty(self):
        assert self._fetch(_mock_response(content_b64=_b64("schema: 2\nname: x\n"))) == {}

    def test_missing_required_field_returns_empty(self):
        yaml_text = "schema: 1\nname: x\nrepo_type: github\n"
        assert self._fetch(_mock_response(content_b64=_b64(yaml_text))) == {}


class TestLoadSoftwareCatalog:
    def _load(self, entries, packages):
        o = _orch()
        o.catalog_url = "https://api.github.com/repos/o/r/contents/package_config"
        o._fetch_catalog_files = MagicMock(return_value=entries)
        o._fetch_package_config = AsyncMock(side_effect=lambda url: packages[url])
        return asyncio.run(o.load_software_catalog())

    @staticmethod
    def _entry(name, type_="file"):
        return {"name": name, "type": type_, "git_url": name}

    def test_skips_non_files_and_non_yaml(self):
        catalog = self._load(
            [self._entry("a.yaml"), self._entry("sub", "dir"), self._entry("README.md")],
            {"a.yaml": {"name": "A"}},
        )
        assert list(catalog) == ["A"]

    def test_bad_file_is_skipped_not_fatal(self):
        catalog = self._load(
            [self._entry("bad.yaml"), self._entry("good.yml")],
            {"bad.yaml": {}, "good.yml": {"name": "Good"}},
        )
        assert list(catalog) == ["Good"]

    def test_duplicate_name_keeps_first_and_logs(self, caplog):
        catalog = self._load(
            [self._entry("a.yaml"), self._entry("b.yaml")],
            {"a.yaml": {"name": "X", "repo_url": "a"}, "b.yaml": {"name": "X", "repo_url": "b"}},
        )
        assert catalog["X"]["repo_url"] == "a"
        assert "already used by another catalog file" in caplog.text

    def test_no_valid_packages_raises(self):
        with pytest.raises(RuntimeError, match="No valid packages"):
            self._load([self._entry("bad.yaml")], {"bad.yaml": {}})


class TestGithubHeaders:
    def test_token_only_sent_to_api_github_com(self):
        o = _orch({"api_credentials": {"github": {"token": "t"}}})
        assert o._github_headers("https://api.github.com/x", "a")["Authorization"] == "token t"
        assert "Authorization" not in o._github_headers("https://example.com/x", "a")


class TestOutputDirName:
    def test_uses_repo_name_from_repo_url(self):
        package = {"repo_url": "https://github.com/HDFGroup/hdf5"}
        assert MetricsOrchestrator._output_dir_name("HDF5", package) == "hdf5"

    def test_strips_git_suffix_and_trailing_slash(self):
        package = {"repo_url": "https://github.com/o/tool.git/"}
        assert MetricsOrchestrator._output_dir_name("Tool", package) == "tool"

    def test_free_text_name_never_becomes_the_path(self):
        package = {"repo_url": "https://github.com/corsa-center/metrics"}
        assert MetricsOrchestrator._output_dir_name("CORSA Metrics Framework", package) == "metrics"
