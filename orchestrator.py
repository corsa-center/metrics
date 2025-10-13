#!/usr/bin/env python3
"""
Orchestrator: Coordinates metrics collection and dashboard integration

This script:
1. Reads the software catalog from dashboard repository
2. Collects metrics for each software package
3. Transforms data to dashboard format
4. Exports to dashboard repository

Usage:
    python orchestrator.py --config config.yaml [--dry-run] [--software HDF5]
"""

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

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
        self.dashboard_path = Path(
            self.config.get("dashboard_repo_path", "../dashboard")
        )
        self.output_path = Path(self.config.get("output_path", "./output"))
        self.collectors_enabled = self.config.get("collectors", {})

    def _load_config(self, config_path: str) -> Dict:
        """Load configuration from YAML file"""
        with open(config_path, "r") as f:
            return yaml.safe_load(f)

    def load_software_catalog(self) -> Dict:
        """Load software catalog from dashboard repository

        Returns:
            Dictionary of software packages with metadata
        """
        catalog_path = self.dashboard_path / "explore/github-data/intReposInfo.json"
        logger.info(f"Loading catalog from {catalog_path}")

        if not catalog_path.exists():
            logger.error(f"Catalog not found at {catalog_path}")
            return {}

        with open(catalog_path, "r") as f:
            data = json.load(f)
            return data.get("data", {})

    def load_cass_mapping(self) -> Dict:
        """Load CASS category mapping"""
        mapping_path = self.dashboard_path / "catalog/cass_category_mapping.json"

        if not mapping_path.exists():
            logger.warning(f"CASS mapping not found at {mapping_path}")
            return {}

        with open(mapping_path, "r") as f:
            data = json.load(f)
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
        cass_mapping = self.load_cass_mapping()

        # Find which category each software belongs to
        repo_to_category = {}
        for category, repos in cass_mapping.items():
            for repo in repos:
                repo_to_category[repo] = category

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
                "category": repo_to_category.get(repo_name, "Uncategorized"),
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
        """Collect Impact dimension metrics

        Args:
            package: Package metadata dictionary

        Returns:
            Impact dimension metrics dictionary
        """
        if not self.collectors_enabled.get("impact", False):
            return {"dimension": "impact", "score": 0.0, "max_score": 100.0}

        logger.info(f"Collecting Impact dimension for {package['name']}")

        try:
            # Try citation collector first (if implemented)
            from collectors.impact.citation import CitationMetricCollector

            collector = CitationMetricCollector(self.config)
            result = await collector.collect(package)
            return {"dimension": "impact", "score": result.get("score", 0), "max_score": 100.0}
        except ImportError:
            # Fall back to dimension placeholder
            try:
                from collectors.impact.dimension import ImpactDimensionCollector

                collector = ImpactDimensionCollector(self.config)
                return await collector.collect(package)
            except Exception as e:
                logger.error(f"Impact dimension collection failed for {package['name']}: {e}")
                return {"dimension": "impact", "score": 0.0, "max_score": 100.0}

    async def collect_community_dimension(self, package: Dict) -> Dict:
        """Collect Community dimension metrics"""
        if not self.collectors_enabled.get("community", False):
            return {"dimension": "community", "score": 0.0, "max_score": 100.0}

        logger.info(f"Collecting Community dimension for {package['name']}")

        try:
            from collectors.community.dimension import CommunityDimensionCollector

            collector = CommunityDimensionCollector(self.config)
            return await collector.collect(package)
        except Exception as e:
            logger.error(f"Community dimension collection failed for {package['name']}: {e}")
            return {"dimension": "community", "score": 0.0, "max_score": 100.0}

    async def collect_viability_dimension(self, package: Dict) -> Dict:
        """Collect Viability dimension metrics"""
        if not self.collectors_enabled.get("viability", False):
            return {"dimension": "viability", "score": 0.0, "max_score": 100.0}

        logger.info(f"Collecting Viability dimension for {package['name']}")

        try:
            # Try licensing collector first (if implemented)
            from collectors.viability.licensing import LicensingCollector

            collector = LicensingCollector(self.config)
            result = await collector.collect(package)
            # Use clarity_score as the viability score for now
            return {"dimension": "viability", "score": result.get("clarity_score", 0), "max_score": 100.0}
        except ImportError:
            # Fall back to dimension placeholder
            try:
                from collectors.viability.dimension import ViabilityDimensionCollector

                collector = ViabilityDimensionCollector(self.config)
                return await collector.collect(package)
            except Exception as e:
                logger.error(f"Viability dimension collection failed for {package['name']}: {e}")
                return {"dimension": "viability", "score": 0.0, "max_score": 100.0}

    async def collect_quality_dimension(self, package: Dict) -> Dict:
        """Collect Quality dimension metrics"""
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
        """Collect all metrics for a package

        Args:
            package: Package metadata

        Returns:
            Complete metrics dictionary
        """
        logger.info(
            f"Starting metrics collection for {package['name']} ({package['repository']})"
        )

        # Collect all 4 CASS dimensions in parallel
        impact_task = self.collect_impact_dimension(package)
        community_task = self.collect_community_dimension(package)
        viability_task = self.collect_viability_dimension(package)
        quality_task = self.collect_quality_dimension(package)

        (
            impact_metrics,
            community_metrics,
            viability_metrics,
            quality_metrics,
        ) = await asyncio.gather(
            impact_task,
            community_task,
            viability_task,
            quality_task,
            return_exceptions=True,
        )

        # Handle exceptions
        if isinstance(impact_metrics, Exception):
            logger.error(f"Impact dimension error: {impact_metrics}")
            impact_metrics = {"dimension": "impact", "score": 0.0, "max_score": 100.0}

        if isinstance(community_metrics, Exception):
            logger.error(f"Community dimension error: {community_metrics}")
            community_metrics = {"dimension": "community", "score": 0.0, "max_score": 100.0}

        if isinstance(viability_metrics, Exception):
            logger.error(f"Viability dimension error: {viability_metrics}")
            viability_metrics = {"dimension": "viability", "score": 0.0, "max_score": 100.0}

        if isinstance(quality_metrics, Exception):
            logger.error(f"Quality dimension error: {quality_metrics}")
            quality_metrics = {"dimension": "quality", "score": 0.0, "max_score": 100.0}

        # Calculate overall score (weighted average of 4 dimensions)
        overall_score = self._calculate_overall_score(
            impact_metrics,
            community_metrics,
            viability_metrics,
            quality_metrics,
        )

        return {
            "overall_score": overall_score,
            "dimensions": {
                "impact": impact_metrics,
                "community": community_metrics,
                "viability": viability_metrics,
                "quality": quality_metrics,
            },
            "last_updated": datetime.utcnow().isoformat() + "Z",
        }

    def _calculate_overall_score(
        self,
        impact: Dict,
        community: Dict,
        viability: Dict,
        quality: Dict,
    ) -> int:
        """Calculate weighted overall sustainability score based on 4 CASS dimensions

        Default weights (can be configured):
        - Impact: 25%
        - Community: 25%
        - Viability: 25%
        - Quality: 25%
        """
        # Get weights from config or use defaults
        weights = self.config.get("metric_weights", {})
        impact_weight = weights.get("impact", 0.25)
        community_weight = weights.get("community", 0.25)
        viability_weight = weights.get("viability", 0.25)
        quality_weight = weights.get("quality", 0.25)

        # Extract scores from dimension results
        impact_score = impact.get("score", 0)
        community_score = community.get("score", 0)
        viability_score = viability.get("score", 0)
        quality_score = quality.get("score", 0)

        # Calculate weighted average
        overall = (
            impact_score * impact_weight
            + community_score * community_weight
            + viability_score * viability_weight
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
            self._write_dashboard_output(all_metrics)
            self._write_summary_report(all_metrics)

        return all_metrics

    def _write_dashboard_output(self, metrics: Dict):
        """Write metrics in dashboard format"""
        output_file = (
            self.dashboard_path / "explore/github-data/sustainabilityMetrics.json"
        )
        output_file.parent.mkdir(parents=True, exist_ok=True)

        logger.info(f"Writing dashboard output to {output_file}")
        with open(output_file, "w") as f:
            json.dump(metrics, f, indent=2)

        logger.info(f"✓ Dashboard metrics written: {len(metrics)} packages")

    def _write_summary_report(self, metrics: Dict):
        """Write summary report"""
        output_file = self.output_path / "orchestrator_summary.json"
        output_file.parent.mkdir(parents=True, exist_ok=True)

        summary = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
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

        logger.info(f"✓ Summary report written to {output_file}")


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
