"""Unit tests for the per-sub-collector toggles in config/orchestrator.yaml.

These blocks were present in the config but never read by the orchestrator;
these tests pin the wiring so they cannot silently go dead again.
"""

from pathlib import Path

import pytest
import yaml

from collectors.ecosystem.dimension import EcosystemDimensionCollector
from collectors.quality.dimension import QualityDimensionCollector

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG = REPO_ROOT / "config" / "orchestrator.yaml"
DIMENSIONS = {"ecosystem": EcosystemDimensionCollector, "quality": QualityDimensionCollector}


def _dims(config):
    return {name: cls(config) for name, cls in DIMENSIONS.items()}


class TestSubEnabled:
    def test_absent_config_defaults_to_enabled(self):
        d = _dims({})
        assert d["ecosystem"].sub_enabled("licensing") is True
        assert d["quality"].sub_enabled("ci_cd") is True

    def test_absent_key_defaults_to_enabled(self):
        d = _dims({"ecosystem_collectors": {"licensing": False}})
        assert d["ecosystem"].sub_enabled("licensing") is False
        assert d["ecosystem"].sub_enabled("engagement") is True

    def test_groups_are_independent(self):
        d = _dims(
            {
                "ecosystem_collectors": {"licensing": False},
                "quality_collectors": {"ci_cd": False},
            }
        )
        assert d["ecosystem"].sub_enabled("licensing") is False
        assert d["quality"].sub_enabled("licensing") is True


class TestConfigMatchesCode:
    """Every toggle in the shipped config must be one a dimension reads."""

    @staticmethod
    def _wired_keys(group):
        return DIMENSIONS[group].known_keys()

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
