#!/usr/bin/env python3
"""
Orchestrator: Coordinates metrics collection and dashboard integration

Collects metrics across the three CASS dimensions defined in the
CASS Sustainability Metrics Report v3:
  - Impact (4.1): Citation, adoption, field research impact
  - Sustainability (4.2): Governance, licensing, maintenance, engagement, etc.
  - Quality (4.3): Reliability, dev practices, reproducibility, usability, etc.

Usage:
    python orchestrator.py --config config.yaml [--dry-run] [--software HDF5]
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import httpx
import re
import yaml

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class MetricsOrchestrator:
    """Orchestrates metrics collection and dashboard integration"""

    def __init__(self, config_path: str):
        """Initialize orchestrator with configuration

        Args:
            config_path: Path to configuration file
        """
        self.config = self._load_config(config_path)
        self.dashboard_base_url = self.config.get(
            "dashboard_base_url", "https://corsa.center/dashboard"
        ).rstrip("/")
        self.output_path = Path(self.config.get("output_path", "./output"))
        self.collectors_enabled = self.config.get("collectors", {})

    def _load_config(self, config_path: str) -> Dict:
        """Load configuration from YAML file, resolving ${ENV_VAR} references"""
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
        return self._resolve_env_vars(config)

    def _resolve_env_vars(self, obj):
        """Recursively resolve ${ENV_VAR} references in config values"""
        if isinstance(obj, str):
            return re.sub(
                r"\$\{(\w+)\}",
                lambda m: os.environ.get(m.group(1), ""),
                obj,
            )
        if isinstance(obj, dict):
            return {k: self._resolve_env_vars(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._resolve_env_vars(v) for v in obj]
        return obj

    def _fetch_json(self, url: str) -> Optional[Dict]:
        """Fetch a JSON file from a URL

        Args:
            url: URL to fetch

        Returns:
            Parsed JSON as dict, or None on failure
        """
        logger.info(f"Fetching {url}")
        try:
            response = httpx.get(url, timeout=30.0, follow_redirects=True)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to fetch {url}: {e}")
            return None

    def load_software_catalog(self) -> Dict:
        """Load software catalog from the dashboard (fetched via HTTP)

        Returns:
            Dictionary of software packages with metadata
        """
        url = f"{self.dashboard_base_url}/explore/github-data/intReposInfo.json"
        data = self._fetch_json(url)
        if data is None:
            return {}
        return data.get("data", {})

    def prepare_software_list(
        self, filter_software: Optional[str] = None
    ) -> List[Dict]:
        """Prepare list of software packages to process

        Args:
            filter_software: Optional name filter for specific software

        Returns:
            List of software packages with required metadata
        """
        catalog = self.load_software_catalog()

        software_list = []
        for repo_name, metadata in catalog.items():
            # Apply filter if specified
            if filter_software and filter_software.lower() not in repo_name.lower():
                continue

            package = {
                "name": metadata.get("name", repo_name),
                "repository": repo_name,
                "repo_url": metadata.get("url", f"https://github.com/{repo_name}"),
                "description": metadata.get("description", ""),
                "homepage": metadata.get("homepageUrl"),
                "license": metadata.get("licenseInfo", {}).get("spdxId"),
                "primary_language": metadata.get("primaryLanguage", {}).get("name")
                if metadata.get("primaryLanguage")
                else None,
            }
            software_list.append(package)

        logger.info(f"Prepared {len(software_list)} software packages for processing")
        return software_list

    async def collect_impact_dimension(self, package: Dict) -> Dict:
        """Collect Impact dimension metrics (CASS Report Section 4.1)

        Args:
            package: Package metadata dictionary

        Returns:
            Impact dimension metrics dictionary
        """
        if not self.collectors_enabled.get("impact", False):
            return {"dimension": "impact", "score": 0.0, "max_score": 100.0}

        logger.info(f"Collecting Impact dimension for {package['name']}")

        try:
            from collectors.impact.citation import CitationMetricCollector

            collector = CitationMetricCollector(self.config)
            result = await collector.collect(package)
            score = result.get("score", 0) if result else 0
            return {
                "dimension": "impact",
                "score": score,
                "max_score": 100.0,
                "sub_results": result,
            }
        except ImportError:
            pass
        except Exception as e:
            logger.error(f"Citation collection failed for {package['name']}: {e}")

        # Fall back to dimension placeholder
        try:
            from collectors.impact.dimension import ImpactDimensionCollector

            collector = ImpactDimensionCollector(self.config)
            return await collector.collect(package)
        except Exception as e:
            logger.error(f"Impact dimension collection failed for {package['name']}: {e}")
            return {"dimension": "impact", "score": 0.0, "max_score": 100.0}

    def _get_github_token(self) -> Optional[str]:
        """Extract GitHub token from resolved config"""
        token = self.config.get("api_credentials", {}).get("github", {}).get("token", "")
        return token if token else None

    async def collect_sustainability_dimension(self, package: Dict) -> Dict:
        """Collect Sustainability dimension metrics (CASS Report Section 4.2)

        Runs all implemented sustainability sub-collectors and combines scores.
        """
        if not self.collectors_enabled.get("sustainability", False):
            return {"dimension": "sustainability", "score": 0.0, "max_score": 100.0}

        logger.info(f"Collecting Sustainability dimension for {package['name']}")

        github_token = self._get_github_token()
        sub_results = {}

        # 4.2.1 CoC, Governance, and Contributor Guidelines
        try:
            from collectors.sustainability.community_health import CommunityHealthCollector
            collector = CommunityHealthCollector(github_token=github_token)
            sub_results["governance"] = await collector.collect(package)
        except Exception as e:
            logger.warning(f"Governance collection failed for {package['name']}: {e}")

        # 4.2.2 Licensing and FAIR Compliance
        try:
            from collectors.sustainability.licensing import LicensingCollector
            collector = LicensingCollector(github_token=github_token)
            sub_results["licensing"] = await collector.collect(package)
        except Exception as e:
            logger.warning(f"Licensing collection failed for {package['name']}: {e}")

        # Calculate combined sustainability score from available sub-collectors
        scores = []
        if "governance" in sub_results:
            scores.append(sub_results["governance"].get("overall_score", {}).get("percentage", 0))
        if "licensing" in sub_results:
            scores.append(sub_results["licensing"].get("compliance_score", {}).get("percentage", 0))

        avg_score = sum(scores) / len(scores) if scores else 0.0

        return {
            "dimension": "sustainability",
            "score": round(avg_score, 2),
            "max_score": 100.0,
            "sub_results": sub_results,
        }

    async def collect_quality_dimension(self, package: Dict) -> Dict:
        """Collect Quality dimension metrics (CASS Report Section 4.3)

        Includes: reliability, development practices, reproducibility,
        usability, accessibility, maintainability, performance.
        """
        if not self.collectors_enabled.get("quality", False):
            return {"dimension": "quality", "score": 0.0, "max_score": 100.0}

        logger.info(f"Collecting Quality dimension for {package['name']}")

        try:
            from collectors.quality.dimension import QualityDimensionCollector

            collector = QualityDimensionCollector(self.config)
            return await collector.collect(package)
        except Exception as e:
            logger.error(f"Quality dimension collection failed for {package['name']}: {e}")
            return {"dimension": "quality", "score": 0.0, "max_score": 100.0}

    async def collect_all_metrics(self, package: Dict) -> Dict:
        """Collect all metrics for a package across the 3 CASS dimensions

        Args:
            package: Package metadata

        Returns:
            Complete metrics dictionary
        """
        logger.info(
            f"Starting metrics collection for {package['name']} ({package['repository']})"
        )

        # Collect all 3 CASS dimensions in parallel
        (
            impact_metrics,
            sustainability_metrics,
            quality_metrics,
        ) = await asyncio.gather(
            self.collect_impact_dimension(package),
            self.collect_sustainability_dimension(package),
            self.collect_quality_dimension(package),
            return_exceptions=True,
        )

        # Handle exceptions
        if isinstance(impact_metrics, Exception):
            logger.error(f"Impact dimension error: {impact_metrics}")
            impact_metrics = {"dimension": "impact", "score": 0.0, "max_score": 100.0}

        if isinstance(sustainability_metrics, Exception):
            logger.error(f"Sustainability dimension error: {sustainability_metrics}")
            sustainability_metrics = {"dimension": "sustainability", "score": 0.0, "max_score": 100.0}

        if isinstance(quality_metrics, Exception):
            logger.error(f"Quality dimension error: {quality_metrics}")
            quality_metrics = {"dimension": "quality", "score": 0.0, "max_score": 100.0}

        # Calculate overall score (weighted average of 3 dimensions)
        overall_score = self._calculate_overall_score(
            impact_metrics,
            sustainability_metrics,
            quality_metrics,
        )

        return {
            "overall_score": overall_score,
            "dimensions": {
                "impact": impact_metrics,
                "sustainability": sustainability_metrics,
                "quality": quality_metrics,
            },
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    def _calculate_overall_score(
        self,
        impact: Dict,
        sustainability: Dict,
        quality: Dict,
    ) -> int:
        """Calculate weighted overall sustainability score based on 3 CASS dimensions

        Default weights (can be configured):
        - Impact: 33%
        - Sustainability: 34%
        - Quality: 33%
        """
        # Get weights from config or use defaults
        weights = self.config.get("metric_weights", {})
        impact_weight = weights.get("impact", 0.33)
        sustainability_weight = weights.get("sustainability", 0.34)
        quality_weight = weights.get("quality", 0.33)

        # Extract scores from dimension results
        impact_score = impact.get("score", 0)
        sustainability_score = sustainability.get("score", 0)
        quality_score = quality.get("score", 0)

        # Calculate weighted average
        overall = (
            impact_score * impact_weight
            + sustainability_score * sustainability_weight
            + quality_score * quality_weight
        )

        return int(round(overall))

    async def process_all_software(
        self, filter_software: Optional[str] = None, dry_run: bool = False
    ) -> Dict:
        """Process all software packages

        Args:
            filter_software: Optional filter for specific software
            dry_run: If True, don't write output files

        Returns:
            Dictionary of all metrics keyed by repository name
        """
        software_list = self.prepare_software_list(filter_software)
        all_metrics = {}

        for i, package in enumerate(software_list, 1):
            logger.info(f"Processing {i}/{len(software_list)}: {package['name']}")

            try:
                metrics = await self.collect_all_metrics(package)
                all_metrics[package["repository"]] = metrics

                # Rate limiting
                if i < len(software_list):
                    await asyncio.sleep(2)  # 2 second delay between packages

            except Exception as e:
                logger.error(f"Failed to process {package['name']}: {e}")
                continue

        # Write output
        if not dry_run:
            self._write_summary_report(all_metrics)
            dashboard_metrics = self._transform_for_dashboard(all_metrics)
            self._write_dashboard_output(dashboard_metrics)

        return all_metrics

    def _write_summary_report(self, metrics: Dict):
        """Write summary report"""
        output_file = self.output_path / "orchestrator_summary.json"
        output_file.parent.mkdir(parents=True, exist_ok=True)

        summary = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_packages": len(metrics),
            "avg_overall_score": sum(m["overall_score"] for m in metrics.values())
            / len(metrics)
            if metrics
            else 0,
            "packages_by_score": {
                "excellent (80-100)": sum(
                    1 for m in metrics.values() if m["overall_score"] >= 80
                ),
                "good (60-79)": sum(
                    1 for m in metrics.values() if 60 <= m["overall_score"] < 80
                ),
                "fair (40-59)": sum(
                    1 for m in metrics.values() if 40 <= m["overall_score"] < 60
                ),
                "needs_improvement (0-39)": sum(
                    1 for m in metrics.values() if m["overall_score"] < 40
                ),
            },
            "top_packages": sorted(
                [(k, v["overall_score"]) for k, v in metrics.items()],
                key=lambda x: x[1],
                reverse=True,
            )[:10],
        }

        with open(output_file, "w") as f:
            json.dump(summary, f, indent=2)

        logger.info(f"Summary report written to {output_file}")

    def _transform_for_dashboard(self, all_metrics: Dict) -> Dict:
        """Transform internal metrics format to the dashboard's sustainabilityMetrics.json format.

        The dashboard JS (catalog.js renderSustainabilityMetrics) expects:
          { "owner/repo": { overall_score, impact_metrics, community_metrics, licensing_metrics, last_updated } }
        """
        dashboard = {}

        for repo_name, metrics in all_metrics.items():
            dims = metrics.get("dimensions", {})

            # --- Licensing metrics transform ---
            licensing_raw = (
                dims.get("sustainability", {})
                .get("sub_results", {})
                .get("licensing", {})
            )
            analysis = licensing_raw.get("license_analysis", {})
            compliance = licensing_raw.get("compliance_score", {})

            # Map category to compatibility label used by the dashboard CSS classes
            category = (analysis.get("category") or "").lower()
            if category in ("permissive", "public domain"):
                compat = "high"
            elif "weak" in category:
                compat = "medium"
            elif category in ("copyleft",):
                compat = "low"
            else:
                compat = "unknown"

            spdx_id = analysis.get("spdx_id") or ""
            # GitHub returns "NOASSERTION" when it can't match a standard license
            if spdx_id in ("NOASSERTION", ""):
                spdx_id = None

            licensing_metrics = {
                "license": spdx_id or analysis.get("license_type") or "Unknown",
                "license_compatibility": compat,
                "outbound_licenses": [],
                "license_clarity_score": int(round(compliance.get("percentage", 0))),
            }

            # --- Impact metrics (stub / from sub_results when available) ---
            impact_raw = dims.get("impact", {}).get("sub_results") or {}
            impact_metrics = {
                "citation_score": impact_raw.get("citation_score", 0.0),
                "formal_citations": impact_raw.get("formal_citations", 0),
                "informal_mentions": impact_raw.get("informal_mentions", 0),
                "dependent_packages": impact_raw.get("dependent_packages", 0),
                "doi_resolutions": impact_raw.get("doi_resolutions", 0),
            }

            # --- Community metrics (stub / from sub_results when available) ---
            governance_raw = (
                dims.get("sustainability", {})
                .get("sub_results", {})
                .get("governance", {})
            )
            community_metrics = {
                "total_contributors": governance_raw.get("total_contributors", 0),
                "active_contributors_30d": governance_raw.get("active_contributors_30d", 0),
                "commit_frequency_per_month": governance_raw.get("commit_frequency_per_month", 0.0),
                "avg_issue_response_days": governance_raw.get("avg_issue_response_days", 0.0),
                "avg_pr_merge_days": governance_raw.get("avg_pr_merge_days", 0.0),
            }

            dashboard[repo_name] = {
                "overall_score": metrics.get("overall_score", 0),
                "impact_metrics": impact_metrics,
                "community_metrics": community_metrics,
                "licensing_metrics": licensing_metrics,
                "last_updated": metrics.get("last_updated", datetime.now(timezone.utc).isoformat()),
            }

        return dashboard

    def _write_dashboard_output(self, dashboard_metrics: Dict):
        """Write sustainabilityMetrics.json to the output directory.

        This file is published to GitHub Pages so the dashboard can fetch it.
        Merges new results into any existing file so that running for a
        single package doesn't erase data for other packages.
        """
        output_file = self.output_path / "sustainabilityMetrics.json"

        # Load existing data so a single-package run is additive
        existing = {}
        if output_file.exists():
            try:
                with open(output_file, "r") as f:
                    existing = json.load(f)
            except (json.JSONDecodeError, OSError):
                existing = {}

        existing.update(dashboard_metrics)

        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w") as f:
            json.dump(existing, f, indent=2)

        logger.info(f"Dashboard metrics written to {output_file}")


async def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Orchestrate metrics collection for dashboard"
    )
    parser.add_argument(
        "--config",
        default="config/orchestrator.yaml",
        help="Path to configuration file",
    )
    parser.add_argument("--software", help="Filter to specific software (e.g., HDF5)")
    parser.add_argument(
        "--dry-run", action="store_true", help="Perform dry run without writing outputs"
    )

    args = parser.parse_args()

    try:
        orchestrator = MetricsOrchestrator(args.config)
        metrics = await orchestrator.process_all_software(
            filter_software=args.software, dry_run=args.dry_run
        )

        logger.info(f"\n{'='*60}")
        logger.info(f"Orchestration complete!")
        logger.info(f"Processed {len(metrics)} packages")
        logger.info(f"{'='*60}\n")

    except Exception as e:
        logger.error(f"Orchestration failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
