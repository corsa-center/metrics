"""Unit tests for per-project metric config: precedence in _sub_enabled,
_package_excluded_keys provenance, and fetching/validating a project's own
metrics file (.metrics/metrics.yaml).
"""

import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock, patch

from orchestrator import MetricsOrchestrator, _sanitize_metric_config


def _orch(config=None):
    o = MetricsOrchestrator.__new__(MetricsOrchestrator)
    o.config = config or {}
    o.ecosystem_collectors = (config or {}).get("ecosystem_collectors", {})
    o.quality_collectors = (config or {}).get("quality_collectors", {})
    o.project_config = (config or {}).get("project_config", {})
    return o


class TestSubEnabledPrecedence:
    def test_package_config_narrows_global_default(self):
        o = _orch()
        package = {"package_config": {"collectors": {"ecosystem": {"funding": False}}}}
        assert o._sub_enabled("ecosystem", "funding", package) is False
        assert o._sub_enabled("ecosystem", "licensing", package) is True

    def test_project_config_narrows_global_default(self):
        o = _orch()
        package = {"project_config": {"collectors": {"quality": {"supply_chain": False}}}}
        assert o._sub_enabled("quality", "supply_chain", package) is False

    def test_global_off_wins_over_package_attempting_to_enable(self):
        o = _orch({"ecosystem_collectors": {"licensing": False}})
        package = {"package_config": {"collectors": {"ecosystem": {"licensing": True}}}}
        assert o._sub_enabled("ecosystem", "licensing", package) is False

    def test_no_package_context_behaves_like_global_only(self):
        o = _orch({"ecosystem_collectors": {"licensing": False}})
        assert o._sub_enabled("ecosystem", "licensing") is False
        assert o._sub_enabled("ecosystem", "engagement") is True

    def test_groups_are_independent_under_package_config(self):
        o = _orch()
        package = {"project_config": {"collectors": {"ecosystem": {"licensing": False}}}}
        assert o._sub_enabled("ecosystem", "licensing", package) is False
        assert o._sub_enabled("quality", "licensing", package) is True

    def test_absent_package_config_defaults_to_enabled(self):
        o = _orch()
        assert o._sub_enabled("ecosystem", "funding", {}) is True


class TestPackageExcludedKeys:
    def test_reports_only_package_level_exclusions(self):
        o = _orch({"ecosystem_collectors": {"engagement": False}})
        package = {"package_config": {"collectors": {"ecosystem": {"funding": False}}}}
        excluded = o._package_excluded_keys("ecosystem", package)
        assert excluded == ["funding"]
        assert "engagement" not in excluded  # globally off, not a package exclusion

    def test_project_and_package_config_both_contribute(self):
        o = _orch()
        package = {
            "package_config": {"collectors": {"ecosystem": {"funding": False}}},
            "project_config": {"collectors": {"ecosystem": {"welcomeness": False}}},
        }
        assert o._package_excluded_keys("ecosystem", package) == ["funding", "welcomeness"]

    def test_no_exclusions_returns_empty_list(self):
        o = _orch()
        assert o._package_excluded_keys("quality", {}) == []

    def test_typo_d_key_has_no_effect_and_is_not_reported(self):
        # "supplly_chain" isn't a real collector -- _sub_enabled would never
        # look it up, so it must not show up as a deliberate exclusion.
        o = _orch()
        package = {"package_config": {"collectors": {"quality": {"supplly_chain": False}}}}
        assert o._package_excluded_keys("quality", package) == []
        # And the real collector it was probably meant to name is unaffected.
        assert o._sub_enabled("quality", "supply_chain", package) is True


class TestSanitizeMetricConfig:
    def test_passes_through_well_formed_config(self):
        data = {
            "schema": 1,
            "collectors": {"quality": {"supply_chain": False}},
            "overrides": {"4.2.8": {"NIH R50 Award Tracking": "N/A"}},
        }
        assert _sanitize_metric_config(data) == data

    def test_null_collectors_block_is_dropped_not_raised(self):
        result = _sanitize_metric_config({"schema": 1, "collectors": None})
        assert "collectors" not in result

    def test_null_overrides_block_is_dropped_not_raised(self):
        result = _sanitize_metric_config({"schema": 1, "overrides": None})
        assert "overrides" not in result

    def test_wrong_type_group_value_is_dropped(self):
        # collectors.quality should be a dict; a string here is a plausible
        # hand-edit mistake and must not survive to be dict-chained later.
        result = _sanitize_metric_config({"collectors": {"quality": "oops"}})
        assert result.get("collectors", {}) == {}

    def test_wrong_type_override_section_is_dropped(self):
        result = _sanitize_metric_config({"overrides": {"4.2.8": "N/A"}})
        assert result.get("overrides", {}) == {}

    def test_non_dict_input_returns_empty(self):
        assert _sanitize_metric_config(None) == {}
        assert _sanitize_metric_config("not a mapping") == {}


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


PACKAGE = {"repository": "HDFGroup/hdf5", "name": "hdf5"}


class TestFetchProjectConfig:
    def test_missing_project_config(self):
        o = _orch()
        with patch("orchestrator.httpx.AsyncClient") as mock_ctor:
            result = asyncio.run(o._fetch_project_config(PACKAGE))
        assert result == {}
        mock_ctor.assert_not_called()

    def test_empty_project_config_disables_project_config(self):
        o = _orch({"project_config": {}})
        with patch("orchestrator.httpx.AsyncClient") as mock_ctor:
            result = asyncio.run(o._fetch_project_config(PACKAGE))
        assert result == {}
        mock_ctor.assert_not_called()

    def test_missing_metrics_file_disables_project_config(self):
        o = _orch({"project_config": {"enabled": True}})
        with patch("orchestrator.httpx.AsyncClient") as mock_ctor:
            result = asyncio.run(o._fetch_project_config(PACKAGE))
        assert result == {}
        mock_ctor.assert_not_called()

    def test_disabled_globally_skips_fetch_entirely(self):
        o = _orch({"project_config": {"enabled": False, "metrics_file": ".metrics/metrics.yaml"}})
        with patch("orchestrator.httpx.AsyncClient") as mock_ctor:
            result = asyncio.run(o._fetch_project_config(PACKAGE))
        assert result == {}
        mock_ctor.assert_not_called()

    def test_missing_file_returns_empty(self):
        o = _orch({"project_config": {"enabled": True, "metrics_file": ".metrics/metrics.yaml"}})
        resp = _mock_response(status_code=404)
        with patch("orchestrator.httpx.AsyncClient", return_value=_mock_async_client(resp)):
            result = asyncio.run(o._fetch_project_config(PACKAGE))
        assert result == {}

    def test_network_error_fails_open(self):
        o = _orch({"project_config": {"enabled": True, "metrics_file": ".metrics/metrics.yaml"}})
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(side_effect=ConnectionError("boom"))
        with patch("orchestrator.httpx.AsyncClient", return_value=cm):
            result = asyncio.run(o._fetch_project_config(PACKAGE))
        assert result == {}

    def test_malformed_yaml_returns_empty(self):
        o = _orch({"project_config": {"enabled": True, "metrics_file": ".metrics/metrics.yaml"}})
        resp = _mock_response(content_b64=_b64("not: valid: yaml: : :"))
        with patch("orchestrator.httpx.AsyncClient", return_value=_mock_async_client(resp)):
            result = asyncio.run(o._fetch_project_config(PACKAGE))
        assert result == {}

    def test_schema_mismatch_returns_empty(self):
        o = _orch({"project_config": {"enabled": True, "metrics_file": ".metrics/metrics.yaml"}})
        yaml_text = "schema: 99\nrepo: HDFGroup/hdf5\n"
        resp = _mock_response(content_b64=_b64(yaml_text))
        with patch("orchestrator.httpx.AsyncClient", return_value=_mock_async_client(resp)):
            result = asyncio.run(o._fetch_project_config(PACKAGE))
        assert result == {}

    def test_repo_mismatch_returns_empty(self):
        o = _orch({"project_config": {"enabled": True, "metrics_file": ".metrics/metrics.yaml"}})
        yaml_text = "schema: 1\nrepo: someone-else/other-repo\n"
        resp = _mock_response(content_b64=_b64(yaml_text))
        with patch("orchestrator.httpx.AsyncClient", return_value=_mock_async_client(resp)):
            result = asyncio.run(o._fetch_project_config(PACKAGE))
        assert result == {}

    def test_valid_config_is_returned(self):
        o = _orch({"project_config": {"enabled": True, "metrics_file": ".metrics/metrics.yaml"}})
        yaml_text = (
            "schema: 1\n"
            "repo: HDFGroup/hdf5\n"
            "collectors:\n"
            "  quality:\n"
            "    supply_chain: false\n"
        )
        resp = _mock_response(content_b64=_b64(yaml_text))
        with patch("orchestrator.httpx.AsyncClient", return_value=_mock_async_client(resp)):
            result = asyncio.run(o._fetch_project_config(PACKAGE))
        assert result["collectors"]["quality"]["supply_chain"] is False

    def test_repo_match_is_case_insensitive(self):
        o = _orch({"project_config": {"enabled": True, "metrics_file": ".metrics/metrics.yaml"}})
        yaml_text = "schema: 1\nrepo: hdfgroup/HDF5\n"
        resp = _mock_response(content_b64=_b64(yaml_text))
        with patch("orchestrator.httpx.AsyncClient", return_value=_mock_async_client(resp)):
            result = asyncio.run(o._fetch_project_config(PACKAGE))
        assert result != {}

    def test_malformed_catalog_key_fails_open(self):
        # repo_name comes from the live-fetched catalog's own keys, not from
        # anything validated beforehand -- one with no "/" must not raise
        # past this method.
        o = _orch({"project_config": {"enabled": True, "metrics_file": ".metrics/metrics.yaml"}})
        bad_package = {"repository": "not-owner-slash-repo", "name": "x"}
        with patch("orchestrator.httpx.AsyncClient") as mock_ctor:
            result = asyncio.run(o._fetch_project_config(bad_package))
        assert result == {}
        mock_ctor.assert_not_called()

    def test_null_collectors_block_is_sanitized_not_raised(self):
        # Regression: a schema-valid file with an empty `collectors:` key
        # (parses to YAML null) previously reached _sub_enabled's dict
        # chaining as None and raised AttributeError instead of failing open.
        o = _orch({"project_config": {"enabled": True, "metrics_file": ".metrics/metrics.yaml"}})
        yaml_text = "schema: 1\nrepo: HDFGroup/hdf5\ncollectors:\n"
        resp = _mock_response(content_b64=_b64(yaml_text))
        with patch("orchestrator.httpx.AsyncClient", return_value=_mock_async_client(resp)):
            result = asyncio.run(o._fetch_project_config(PACKAGE))
        assert "collectors" not in result
        assert o._sub_enabled("quality", "supply_chain", {"project_config": result}) is True

    def test_null_overrides_block_is_sanitized_not_raised(self):
        o = _orch({"project_config": {"enabled": True, "metrics_file": ".metrics/metrics.yaml"}})
        yaml_text = "schema: 1\nrepo: HDFGroup/hdf5\noverrides:\n"
        resp = _mock_response(content_b64=_b64(yaml_text))
        with patch("orchestrator.httpx.AsyncClient", return_value=_mock_async_client(resp)):
            result = asyncio.run(o._fetch_project_config(PACKAGE))
        assert "overrides" not in result


class TestCollectAllMetricsAttachesConfig:
    """The whole feature rests on collect_all_metrics attaching both config
    layers to `package` before the three dimensions read them -- verify the
    wiring itself, not just the precedence logic it feeds.
    """

    def test_both_config_layers_attached_before_dimensions_run(self):
        o = _orch()
        o.output_path = MagicMock()
        seen = {}

        async def fake_dimension(package):
            # Snapshot what each dimension collector would see.
            seen["package_config"] = package.get("package_config")
            seen["project_config"] = package.get("project_config")
            return {"dimension": "x", "score": 0.0, "max_score": 100.0}

        o.collect_impact_dimension = fake_dimension
        o.collect_ecosystem_dimension = fake_dimension
        o.collect_quality_dimension = fake_dimension
        o._calculate_overall_score = lambda *a: 0
        o._load_package_config = MagicMock(return_value={"overrides": {"4.2.8": {}}})

        async def fake_fetch(package):
            return {"collectors": {"quality": {"supply_chain": False}}}

        o._fetch_project_config = fake_fetch

        package = {"repository": "HDFGroup/hdf5", "name": "hdf5"}
        metrics = asyncio.run(o.collect_all_metrics(package))

        assert seen["package_config"] == {"overrides": {"4.2.8": {}}}
        assert seen["project_config"] == {"collectors": {"quality": {"supply_chain": False}}}
        # And it survives onto the returned metrics for _transform_for_dashboard.
        assert metrics["project_config"] == {"collectors": {"quality": {"supply_chain": False}}}
