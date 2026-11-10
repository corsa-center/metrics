"""
Impact Dimension Collector

CASS Sustainability Metrics Report v3 - Section 4.1

The Impact dimension assesses the influence of software based on its adoption,
citation, and integration into research workflows.

Sub-categories (4.1.1 - 4.1.2):
- 4.1.1 Software Citation and Adoption
- 4.1.2 Field Research Impact
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict

from collectors.dimension import DimensionCollector, for_metrics_json, sub_result_metrics

logger = logging.getLogger(__name__)


class ImpactDimensionCollector(DimensionCollector):
    """Collector for Impact dimension metrics (CASS Report Section 4.1).

    Impact has a single sub-collector (citations) and no per-collector
    toggles; the dimension score is the citation score.
    """

    dimension = "impact"

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        if not self.enabled:
            return self.empty_result()

        logger.info(f"Collecting Impact dimension for {package['name']}")

        forge = self.forge_for(package)
        if forge is not None:
            try:
                from collectors.impact.citation import CitationMetricCollector

                result = await CitationMetricCollector(self.config, forge).collect(package)
                return {
                    "dimension": "impact",
                    "score": result.get("score", 0) if result else 0,
                    "max_score": 100.0,
                    "sub_results": result,
                }
            except Exception as e:
                logger.error(f"Citation collection failed for {package['name']}: {e}")

        return self.placeholder(package)

    def raw_metrics(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Impact's sub_results is the citation result itself, so it goes
        under `citation`, matching the other dimensions' layout."""
        metrics: Dict[str, Any] = {"score": result.get("score")}
        if result.get("sub_results"):
            metrics["citation"] = sub_result_metrics(result["sub_results"])
        return for_metrics_json(metrics)

    def placeholder(self, package: Dict[str, Any]) -> Dict[str, Any]:
        """The result when citations couldn't be collected: a zero score
        the report marks as a placeholder."""
        return {
            **self.empty_result(),
            "metadata": {
                "status": "placeholder",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "package_name": package.get("name"),
                "description": "Measures software citation, adoption, and field research impact",
                "sub_categories": [
                    "Software Citation and Adoption",
                    "Field Research Impact",
                ],
            },
        }
