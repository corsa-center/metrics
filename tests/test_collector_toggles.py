"""Unit tests for the per-sub-collector toggles in config/orchestrator.yaml.

These blocks were present in the config but never read by the orchestrator;
these tests pin the wiring so they cannot silently go dead again.
"""

import re
from pathlib import Path

import pytest
import yaml

from orchestrator import MetricsOrchestrator

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG = REPO_ROOT / "config" / "orchestrator.yaml"
ORCHESTRATOR_SRC = (REPO_ROOT / "orchestrator.py").read_text()


def _orch(config):
    o = MetricsOrchestrator.__new__(MetricsOrchestrator)
    o.config = config
    o.collectors_enabled = config.get("collectors", {})
    o.ecosystem_collectors = config.get("ecosystem_collectors", {})
    o.quality_collectors = config.get("quality_collectors", {})
    return o


class TestSubEnabled:
    def test_absent_config_defaults_to_enabled(self):
        o = _orch({})
        assert o._sub_enabled("ecosystem", "licensing") is True
        assert o._sub_enabled("quality", "ci_cd") is True

    def test_absent_key_defaults_to_enabled(self):
        o = _orch({"ecosystem_collectors": {"licensing": False}})
        assert o._sub_enabled("ecosystem", "licensing") is False
        assert o._sub_enabled("ecosystem", "engagement") is True

    def test_groups_are_independent(self):
        o = _orch(
            {
                "ecosystem_collectors": {"licensing": False},
                "quality_collectors": {"ci_cd": False},
            }
        )
        assert o._sub_enabled("ecosystem", "licensing") is False
        assert o._sub_enabled("quality", "licensing") is True


class TestConfigMatchesCode:
    """Every toggle in the shipped config must be one the orchestrator reads."""

    @staticmethod
    def _wired_keys(group):
        # Call sites pass a trailing `package` arg (per-package config
        # precedence), so match on the key alone rather than the closing `)`.
        return set(
            re.findall(rf'_sub_enabled\("{group}", "([a-z_]+)"', ORCHESTRATOR_SRC)
        )

    @pytest.mark.parametrize(
        "group,block",
        [
            ("ecosystem", "ecosystem_collectors"),
            ("quality", "quality_collectors"),
        ],
    )
    def test_no_dead_toggles(self, group, block):
        config = yaml.safe_load(CONFIG.read_text())
        assert set(config[block]) == self._wired_keys(group)

    def test_every_guard_is_documented_in_config(self):
        config = yaml.safe_load(CONFIG.read_text())
        documented = set(config["ecosystem_collectors"]) | set(
            config["quality_collectors"]
        )
        wired = self._wired_keys("ecosystem") | self._wired_keys("quality")
        assert wired == documented
