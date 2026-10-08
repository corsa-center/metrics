"""Unit tests for per-package metric config: precedence in _sub_enabled,
_package_excluded_keys provenance, and fetching/validating a packages own
package configuration file.
"""

import base64
from unittest.mock import AsyncMock, MagicMock

from orchestrator import MetricsOrchestrator, _sanitize_package_config


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
