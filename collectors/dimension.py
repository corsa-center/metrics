"""
Dimension collectors: run a dimension's sub-collectors and combine their
scores into the dimension's score.

Each dimension (collectors/<dimension>/dimension.py) lists its sub-collectors
as SubCollector entries. The list is the single place a sub-collector is
wired in: it names the config toggle that turns it off, where its result is
kept, and how its result becomes a percentage for the dimension average.
"""

import importlib
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from forge.interface import Forge

logger = logging.getLogger(__name__)

# Keys every sub-collector result repeats; metrics.json has them once,
# as `package` and `generated_at`.
_BOOKKEEPING_KEYS = frozenset({"package_name", "repository", "timestamp"})
# What a row was judged on, for report.html (see package_report.py);
# not a measurement.
_EVIDENCE_KEYS = frozenset({"evidence", "row_evidence"})


def without_evidence(value: Any) -> Any:
    """`value` with every evidence entry removed, at any depth."""
    if isinstance(value, dict):
        return {k: without_evidence(v) for k, v in value.items() if k not in _EVIDENCE_KEYS}
    if isinstance(value, list):
        return [without_evidence(v) for v in value]
    return value


def sub_result_metrics(result: Any) -> Any:
    """A sub-collector's result as written to metrics.json: without
    evidence or the per-result bookkeeping keys."""
    if not isinstance(result, dict):
        return result
    return without_evidence({k: v for k, v in result.items() if k not in _BOOKKEEPING_KEYS})


def percentage_at(*keys: str) -> Callable[[Dict], Optional[float]]:
    """A score function reading the number at `keys` in a sub-collector's
    result, or None when it isn't a number (a gapped result's percentage
    is None, and shouldn't count as 0)."""
    def score(result: Dict) -> Optional[float]:
        value: Any = result
        for key in keys:
            value = value.get(key, {}) if isinstance(value, dict) else {}
        return value if isinstance(value, (int, float)) else None
    return score


@dataclass(frozen=True)
class SubCollector:
    """One sub-collector a dimension runs.

    toggle: its key under `<dimension>_collectors:` in config/orchestrator.yaml
        and `collectors: <dimension>:` in a package config.
    result_key: where its result goes in the dimension's sub_results (and
        score_components); differs from `toggle` for older collectors.
    module, cls: the collector class, imported when it runs.
    name: what the logs call it.
    score: its result as a 0-100 percentage for the dimension average, or
        None to leave it out (nothing measured, or everything gapped).
    """
    toggle: str
    result_key: str
    module: str
    cls: str
    name: str
    score: Callable[[Dict], Optional[float]]


class DimensionCollector:
    """Runs a dimension's sub-collectors and averages their percentages.

    Subclasses set `dimension` and `SUB_COLLECTORS`, listed in the order
    the sub-collectors run (also the order of score_components).
    """

    dimension: str = ""
    SUB_COLLECTORS: Tuple[SubCollector, ...] = ()

    def __init__(self, config: Dict[str, Any]):
        self.config = config

    @property
    def enabled(self) -> bool:
        """Whether config's `collectors:` block turns this dimension on."""
        return bool(self.config.get("collectors", {}).get(self.dimension, False))

    @property
    def toggles(self) -> Dict[str, bool]:
        """This dimension's per-sub-collector toggles from config."""
        return self.config.get(f"{self.dimension}_collectors", {})

    @classmethod
    def known_keys(cls) -> set:
        """Every toggle key this dimension reads."""
        return {sub.toggle for sub in cls.SUB_COLLECTORS}

    def empty_result(self) -> Dict[str, Any]:
        return {"dimension": self.dimension, "score": 0.0, "max_score": 100.0}

    def raw_metrics(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """This dimension's entry in metrics.json's `metrics`: its score,
        each sub-collector's percentage (`score_components`), and each
        sub-collector's measurements keyed as in `sub_results`. A dimension
        that was off or skipped has only its score."""
        metrics: Dict[str, Any] = {"score": result.get("score")}
        if "score_components" in result:
            metrics["score_components"] = result["score_components"]
        for key, sub_result in (result.get("sub_results") or {}).items():
            metrics[key] = sub_result_metrics(sub_result)
        return metrics

    def forge_for(self, package: Dict) -> Optional[Forge]:
        """The Forge for a package, chosen by its repo_type (see Forge.for_repo)."""
        return Forge.for_repo(
            package.get("repo_type"),
            package.get("repo_url", ""),
            self.config.get("api_credentials", {}),
        )

    def sub_enabled(self, key: str, package: Optional[Dict] = None) -> bool:
        """Whether an individual sub-collector is enabled for this package.

        Defaults to True when a toggle is absent at every layer. Two layers,
        each only able to narrow the one before it -- none can re-enable a
        collector a higher layer turned off:
          1. Global config/orchestrator.yaml -- applies to every package.
          2. The maintainer's central package_config/<package>.yaml.
        `package` carries (2) once the catalog has attached it; omit it to
        check only (1).
        """
        if not self.toggles.get(key, True):
            return False
        if package is not None:
            narrowed = package.get("collectors", {}).get(self.dimension, {}).get(key)
            if narrowed is False:
                return False
        return True

    def package_excluded_keys(self, package: Dict) -> List[str]:
        """Toggle keys this package's configs turned off that the global
        config would otherwise run -- i.e. exclusions attributable to
        package_config, not to the operator.
        Recorded on the dimension result so a deliberate exclusion is
        distinguishable from a collector that simply crashed.

        A misspelled package_config key is logged and left out: it has no
        effect on collection, so reporting it as an exclusion would mislead.
        """
        known_keys = self.known_keys()
        candidate_keys = set(self.toggles) & known_keys
        mentioned = set(package.get("collectors", {}).get(self.dimension, {}))
        unrecognized = mentioned - known_keys
        if unrecognized:
            logger.warning(
                f"{package.get('repository')}: ignoring unrecognized "
                f"{self.dimension} collector key(s) in package_config: {sorted(unrecognized)}"
            )
        candidate_keys |= mentioned & known_keys
        return sorted(
            key
            for key in candidate_keys
            if self.toggles.get(key, True) and not self.sub_enabled(key, package)
        )

    async def collect(self, package: Dict) -> Dict[str, Any]:
        """Run every enabled sub-collector and average their percentages.

        A sub-collector that raises is logged and left out; one whose score
        is None (everything it measures gapped) is left out of the average
        rather than counted as 0, and so is a result that isn't a dict.
        """
        if not self.enabled:
            return self.empty_result()

        logger.info(f"Collecting {self.dimension.capitalize()} dimension for {package['name']}")

        forge = self.forge_for(package)
        if forge is None:
            return self.empty_result()

        sub_results: Dict[str, Dict] = {}
        for sub in self.SUB_COLLECTORS:
            if not self.sub_enabled(sub.toggle, package):
                continue
            try:
                collector_cls = getattr(importlib.import_module(sub.module), sub.cls)
                sub_results[sub.result_key] = await collector_cls(forge).collect(package)
            except Exception as e:
                logger.warning(f"{sub.name} collection failed for {package['name']}: {e}")

        # (result key, percentage) -- kept by name so the per-project report
        # can show which collectors the average was taken over.
        components: List[Tuple[str, float]] = []
        for sub in self.SUB_COLLECTORS:
            result = sub_results.get(sub.result_key)
            if not isinstance(result, dict):
                continue
            pct = sub.score(result)
            if pct is not None:
                components.append((sub.result_key, pct))

        scores = [value for _, value in components]
        avg_score = sum(scores) / len(scores) if scores else 0.0

        return {
            "dimension": self.dimension,
            "score": round(avg_score, 2),
            "max_score": 100.0,
            "sub_results": sub_results,
            "score_components": dict(components),
            "excluded_by_config": self.package_excluded_keys(package),
        }
