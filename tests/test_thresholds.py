"""Unit tests for the threshold registry (collectors/ecosystem/base.py) and
for config/thresholds.yaml staying in sync with the code that reads it.

The whole point of this registry is that a threshold declared in
config/thresholds.yaml with no get_threshold() call site reading it -- or a
call site referencing a threshold that isn't declared -- can't happen
silently. TestConfigMatchesCode below is the mechanical check for that,
mirroring tests/test_collector_toggles.py's TestConfigMatchesCode for the
same class of bug (a config value nothing reads).
"""

import re
from pathlib import Path

import pytest
import yaml

from collectors.ecosystem.base import ThresholdRegistry, get_threshold

REPO_ROOT = Path(__file__).resolve().parent.parent
THRESHOLDS_YAML = REPO_ROOT / "config" / "thresholds.yaml"

# Every call site across the whole tree, not just orchestrator.py -- both
# collectors and orchestrator.py read from this registry.
_CALL_SITE_RE = re.compile(
    r'get_threshold\(\s*"([^"]+)"\s*,\s*"([^"]+)"(?:\s*,\s*"([^"]+)")?\s*\)'
)


def _find_call_sites():
    """(section, label, param_or_None) for every get_threshold(...) call in
    the tree, scanning source text directly (same approach as
    test_collector_toggles.py) rather than importing every module."""
    sites = []
    for py_file in REPO_ROOT.rglob("*.py"):
        if "/tests/" in str(py_file) or py_file.name.startswith("test_"):
            continue
        if "/venv/" in str(py_file) or "/.venv/" in str(py_file):
            continue
        text = py_file.read_text()
        for section, label, param in _CALL_SITE_RE.findall(text):
            sites.append((section, label, param or None))
    return sites


@pytest.fixture
def registry():
    return ThresholdRegistry(THRESHOLDS_YAML)


class TestRegistryResolution:
    def test_scalar_default(self, registry):
        assert registry.get("4.2.3", "Release Pattern Assessment") == 1

    def test_set_membership_default(self, registry):
        assert registry.get("4.2.3", "Activity Trend Monitoring") == ["stable", "increasing"]

    def test_dict_param_default(self, registry):
        assert registry.get("4.3.2", "CI/CD Effectiveness Assessment", "max_cycle_time_hours") == 168

    def test_unknown_section_raises(self, registry):
        with pytest.raises(KeyError):
            registry.get("9.9.9", "Nothing")

    def test_unknown_label_raises(self, registry):
        with pytest.raises(KeyError):
            registry.get("4.2.3", "Not A Real Sub-Metric")

    def test_unknown_param_raises(self, registry):
        with pytest.raises(KeyError):
            registry.get("4.3.2", "CI/CD Effectiveness Assessment", "not_a_real_param")

    def test_param_on_a_scalar_entry_raises(self, registry):
        # Release Pattern Assessment is a bare number, not a mapping --
        # asking it for a named parameter is a call-site bug.
        with pytest.raises(KeyError):
            registry.get("4.2.3", "Release Pattern Assessment", "anything")


class TestOverridePrecedence:
    def test_scalar_override_replaces_default(self, registry):
        registry.set_overrides({"4.2.3": {"Release Pattern Assessment": 2}})
        assert registry.get("4.2.3", "Release Pattern Assessment") == 2

    def test_override_does_not_leak_to_other_labels(self, registry):
        registry.set_overrides({"4.2.3": {"Release Pattern Assessment": 2}})
        assert registry.get("4.2.3", "Contributor Abandonment Forecasting") == 0.5

    def test_partial_dict_override_leaves_other_params_at_default(self, registry):
        registry.set_overrides({
            "4.3.2": {"CI/CD Effectiveness Assessment": {"max_cycle_time_hours": 48}}
        })
        assert registry.get("4.3.2", "CI/CD Effectiveness Assessment", "max_cycle_time_hours") == 48
        # The sibling param wasn't touched by the override -- confirm it's
        # still at its own default, not dragged along or cleared.
        assert registry.get("4.3.2", "CI/CD Effectiveness Assessment", "min_success_rate_pct") == 60

    def test_unrecognized_section_override_raises(self, registry):
        with pytest.raises(ValueError):
            registry.set_overrides({"9.9.9": {"Nothing": 1}})

    def test_unrecognized_label_override_raises(self, registry):
        with pytest.raises(ValueError):
            registry.set_overrides({"4.2.3": {"Not A Real Sub-Metric": 1}})

    def test_unrecognized_param_override_raises(self, registry):
        with pytest.raises(ValueError):
            registry.set_overrides({
                "4.3.2": {"CI/CD Effectiveness Assessment": {"bogus_param": 1}}
            })

    def test_dict_override_for_a_scalar_default_raises(self, registry):
        with pytest.raises(ValueError):
            registry.set_overrides({"4.2.3": {"Release Pattern Assessment": {"x": 1}}})

    def test_scalar_override_for_a_dict_default_raises(self, registry):
        # The reverse of the case above: a sub-metric with named parameters
        # can't be overridden with a bare value -- silently accepting one
        # would mean get(section, label, param=...) just ignores it, since
        # only a dict override is ever consulted for a specific param.
        with pytest.raises(ValueError):
            registry.set_overrides({"4.3.2": {"CI/CD Effectiveness Assessment": 999}})

    def test_null_override_raises(self, registry):
        with pytest.raises(ValueError):
            registry.set_overrides({"4.2.3": {"Release Pattern Assessment": None}})

    def test_bare_lookup_of_a_dict_default_without_param_raises(self, registry):
        with pytest.raises(KeyError):
            registry.get("4.3.2", "CI/CD Effectiveness Assessment")

    def test_empty_overrides_is_a_noop(self, registry):
        registry.set_overrides({})
        assert registry.get("4.2.3", "Release Pattern Assessment") == 1
        registry.set_overrides(None)
        assert registry.get("4.2.3", "Release Pattern Assessment") == 1


class TestConfigMatchesCode:
    """Every threshold declared in config/thresholds.yaml must be read by
    some get_threshold() call, and every call must reference something
    declared -- the bidirectional version of the check that would have
    caught the three dead constants (_MAX_DEPARTURE_RATE, _MIN_CHANNELS,
    _CYCLE_TIME_ELITE_HOURS) this registry replaced.
    """

    @staticmethod
    def _declared_keys():
        """(section, label, param_or_None) for every default in the YAML --
        a dict-shaped entry expands to one tuple per parameter."""
        defaults = yaml.safe_load(THRESHOLDS_YAML.read_text()) or {}
        keys = []
        for section, labels in defaults.items():
            for label, value in labels.items():
                if isinstance(value, dict):
                    for param in value:
                        keys.append((section, label, param))
                else:
                    keys.append((section, label, None))
        return keys

    def test_every_declared_threshold_has_a_call_site(self):
        declared = set(self._declared_keys())
        called = set(_find_call_sites())
        orphaned = declared - called
        assert not orphaned, (
            f"config/thresholds.yaml declares threshold(s) with no "
            f"get_threshold() call site reading them: {sorted(orphaned)}"
        )

    def test_every_call_site_references_a_declared_threshold(self):
        declared = set(self._declared_keys())
        called = set(_find_call_sites())
        undeclared = called - declared
        assert not undeclared, (
            f"get_threshold() is called for threshold(s) with no default in "
            f"config/thresholds.yaml: {sorted(undeclared)}"
        )

    def test_yaml_loads_and_is_well_formed(self):
        data = yaml.safe_load(THRESHOLDS_YAML.read_text())
        assert isinstance(data, dict)
        for section, labels in data.items():
            assert isinstance(labels, dict), f"section {section!r} must map label -> value"
