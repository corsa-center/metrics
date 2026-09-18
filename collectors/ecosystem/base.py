"""Threshold registry shared by ecosystem/quality collectors and orchestrator.py.

The HTTP plumbing that used to live here (COLLECTION_GAP, RetryingTransport,
GitHubCollectorBase) has moved to the forge/ package -- see forge/base.py
and forge/github.py. This module now only owns pass/fail threshold
resolution, which is unrelated to which platform a repo is hosted on.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml


# --------------------------------------------------------------------------- #
# Threshold registry                                                          #
# --------------------------------------------------------------------------- #
# See config/thresholds.yaml for the values themselves and the full design
# rationale. In short: every configurable pass/fail threshold used to be a
# magic number duplicated (or, in three cases, defined but never actually
# read) across collectors and orchestrator.py's rendering code. This module
# is now the one place that resolves a threshold value, whether the call
# site is a collector deciding its own sub_scores or orchestrator.py
# deciding what to render.

_ThresholdValue = Union[int, float, List[str]]

_THRESHOLDS_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "thresholds.yaml"


class ThresholdRegistry:
    """Resolves a (section, label[, param]) key to its configured value.

    Defaults are loaded once from config/thresholds.yaml. Overrides (from
    config/orchestrator.yaml's own `thresholds:` block today; per-project and
    per-package overrides are not wired up yet) are applied on top via
    `set_overrides`, called once at orchestrator startup -- well before any
    collector's `collect()` runs, so every `get()` call during an actual run
    sees the final, merged value.
    """

    def __init__(self, defaults_path: Path):
        self._defaults_path = defaults_path
        self._defaults: Dict[str, Dict[str, Any]] = self._load(defaults_path)
        self._overrides: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def _load(path: Path) -> Dict[str, Dict[str, Any]]:
        if not path.exists():
            return {}
        data = yaml.safe_load(path.read_text()) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{path} must be a mapping of section -> label -> value")
        return data

    def set_overrides(self, overrides: Optional[Dict[str, Dict[str, Any]]]) -> None:
        """Install config-file overrides, validating each key exists first.

        An override for a (section, label) or (section, label, param) that
        isn't already in the defaults is almost always a typo -- the whole
        point of this registry is that a threshold with nobody reading it
        fails immediately, not silently, so this raises rather than storing
        an override nothing will ever look up.
        """
        overrides = overrides or {}
        for section, labels in overrides.items():
            if not isinstance(labels, dict):
                raise ValueError(
                    f"thresholds override for section {section!r} must be a mapping "
                    f"of sub-metric label -> value"
                )
            default_labels = self._defaults.get(section)
            if default_labels is None:
                raise ValueError(
                    f"thresholds override references section {section!r}, which has "
                    f"no defaults in {self._defaults_path} -- not a recognized "
                    f"threshold section"
                )
            for label, value in labels.items():
                if label not in default_labels:
                    raise ValueError(
                        f"thresholds override references {section!r} / {label!r}, "
                        f"which has no default -- not a recognized threshold"
                    )
                default_value = default_labels[label]
                if value is None:
                    raise ValueError(
                        f"thresholds override for {section!r} / {label!r} is null "
                        f"-- omit the key entirely instead of overriding to no value"
                    )
                if isinstance(default_value, dict):
                    if not isinstance(value, dict):
                        raise ValueError(
                            f"thresholds override for {section!r} / {label!r} must "
                            f"be a mapping of parameter name -> value, since the "
                            f"default has named parameters -- got "
                            f"{type(value).__name__}"
                        )
                    unknown = set(value) - set(default_value)
                    if unknown:
                        raise ValueError(
                            f"thresholds override for {section!r} / {label!r} sets "
                            f"unrecognized parameter(s) {sorted(unknown)}"
                        )
                elif isinstance(value, dict):
                    raise ValueError(
                        f"thresholds override for {section!r} / {label!r} is a "
                        f"mapping, but the default is a single value"
                    )
        self._overrides = overrides

    def get(self, section: str, label: str, param: Optional[str] = None) -> _ThresholdValue:
        """Resolve one threshold value, overrides applied.

        Raises KeyError if (section, label[, param]) has no default at
        all -- that's a call site bug (referencing a threshold this
        registry was never told about), not a config problem, so it's not
        caught by set_overrides's validation and fails at the call site
        instead.
        """
        try:
            default_value = self._defaults[section][label]
        except KeyError:
            raise KeyError(
                f"no threshold default for {section!r} / {label!r} -- add one to "
                f"{self._defaults_path} first"
            ) from None

        override_value = self._overrides.get(section, {}).get(label)

        if isinstance(default_value, dict):
            if param is None:
                raise KeyError(
                    f"threshold {section!r} / {label!r} has named parameters -- "
                    f"call with param=<name>, not a bare lookup"
                )
            if param not in default_value:
                raise KeyError(
                    f"no threshold default for {section!r} / {label!r} / {param!r}"
                )
            if isinstance(override_value, dict) and param in override_value:
                return override_value[param]
            return default_value[param]

        if param is not None:
            raise KeyError(
                f"threshold {section!r} / {label!r} has no parameters "
                f"(it's a single value) -- called with param={param!r}"
            )
        if override_value is not None:
            return override_value
        return default_value


_REGISTRY = ThresholdRegistry(_THRESHOLDS_PATH)


def get_threshold(section: str, label: str, param: Optional[str] = None) -> _ThresholdValue:
    """Module-level convenience wrapper around the shared registry instance."""
    return _REGISTRY.get(section, label, param)


def configure_threshold_overrides(overrides: Optional[Dict[str, Dict[str, Any]]]) -> None:
    """Install threshold overrides from config/orchestrator.yaml's `thresholds:`
    block. Call once at startup, before any collector runs."""
    _REGISTRY.set_overrides(overrides)
