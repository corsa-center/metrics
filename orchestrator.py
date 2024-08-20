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

    async def collect_citation_metrics(self, package: Dict) -> Dict:
        """Collect citation metrics for a package

        Args:
            package: Package metadata dictionary

        Returns:
            Citation metrics dictionary
        """
        if not self.collectors_enabled.get("citation", False):
            return self._empty_citation_metrics()

        logger.info(f"Collecting citation metrics for {package['name']}")

        # Import collector dynamically to avoid import errors if not configured
        try:
            from collectors.impact.citation import CitationMetricCollector

            collector = CitationMetricCollector(self.config)
            result = await collector.collect(package)

            return {
                "citation_score": result.get("score", 0),
                "formal_citations": result.get("sub_metrics", {})
                .get("formal_citations", {})
                .get("raw_value", 0),
                "informal_mentions": result.get("sub_metrics", {})
                .get("informal_mentions", {})
                .get("raw_value", 0),
                "dependent_packages": result.get("sub_metrics", {})
                .get("dependent_packages", {})
                .get("raw_value", 0),
                "doi_resolutions": result.get("sub_metrics", {})
                .get("doi_resolutions", {})
                .get("raw_value", 0),
            }
        except Exception as e:
            logger.error(
                f"Citation metrics collection failed for {package['name']}: {e}"
            )
            return self._empty_citation_metrics()

    async def collect_community_metrics(self, package: Dict) -> Dict:
        """Collect community health metrics"""
        if not self.collectors_enabled.get("community", False):
            return self._empty_community_metrics()

        logger.info(f"Collecting community metrics for {package['name']}")

        try:
            from collectors.community.health import CommunityHealthCollector

            collector = CommunityHealthCollector(self.config)
            result = await collector.collect(package)

            return {
                "total_contributors": result.get("total_contributors", 0),
                "active_contributors_30d": result.get("active_contributors_30d", 0),
                "commit_frequency_per_month": result.get("commit_frequency", 0),
                "avg_issue_response_days": result.get("avg_issue_response_time", 0),
                "avg_pr_merge_days": result.get("avg_pr_merge_time", 0),
            }
        except Exception as e:
            logger.error(
                f"Community metrics collection failed for {package['name']}: {e}"
            )
            return self._empty_community_metrics()

    async def collect_licensing_metrics(self, package: Dict) -> Dict:
        """Collect licensing metrics"""
        if not self.collectors_enabled.get("licensing", False):
            # Use basic info from catalog
            return {
                "license": package.get("license", "Unknown"),
                "license_compatibility": "unknown",
                "outbound_licenses": [],
                "license_clarity_score": 0,
            }

        logger.info(f"Collecting licensing metrics for {package['name']}")

        try:
            from collectors.viability.licensing import LicensingCollector

            collector = LicensingCollector(self.config)
            result = await collector.collect(package)

            return {
                "license": result.get("license", package.get("license", "Unknown")),
                "license_compatibility": result.get("compatibility", "unknown"),
                "outbound_licenses": result.get("dependencies", []),
                "license_clarity_score": result.get("clarity_score", 0),
            }
        except Exception as e:
            logger.error(
                f"Licensing metrics collection failed for {package['name']}: {e}"
            )
            return {
                "license": package.get("license", "Unknown"),
                "license_compatibility": "unknown",
                "outbound_licenses": [],
                "license_clarity_score": 0,
            }

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

        # Collect metrics in parallel
        citation_task = self.collect_citation_metrics(package)
        community_task = self.collect_community_metrics(package)
        licensing_task = self.collect_licensing_metrics(package)

        citation_metrics, community_metrics, licensing_metrics = await asyncio.gather(
            citation_task, community_task, licensing_task, return_exceptions=True
        )

        # Handle exceptions
        if isinstance(citation_metrics, Exception):
            logger.error(f"Citation collection error: {citation_metrics}")
            citation_metrics = self._empty_citation_metrics()

        if isinstance(community_metrics, Exception):
            logger.error(f"Community collection error: {community_metrics}")
            community_metrics = self._empty_community_metrics()

        if isinstance(licensing_metrics, Exception):
            logger.error(f"Licensing collection error: {licensing_metrics}")
            licensing_metrics = {
                "license": package.get("license", "Unknown"),
                "license_compatibility": "unknown",
                "outbound_licenses": [],
                "license_clarity_score": 0,
            }

        # Calculate overall score (weighted average)
        overall_score = self._calculate_overall_score(
            citation_metrics, community_metrics, licensing_metrics
        )

        return {
            "overall_score": overall_score,
            "impact_metrics": citation_metrics,
            "community_metrics": community_metrics,
            "licensing_metrics": licensing_metrics,
            "last_updated": datetime.utcnow().isoformat() + "Z",
        }

    def _calculate_overall_score(
        self, citation: Dict, community: Dict, licensing: Dict
    ) -> int:
        """Calculate weighted overall sustainability score"""
        # Weights: citation (40%), community (40%), licensing (20%)
        citation_score = citation.get("citation_score", 0)

        # Normalize community metrics (simplified)
        community_score = min(
            100,
            (
                min(100, community.get("active_contributors_30d", 0) * 5) * 0.4
                + min(100, community.get("commit_frequency_per_month", 0) * 2) * 0.3
                + (100 - min(100, community.get("avg_issue_response_days", 10) * 5))
                * 0.3
            ),
        )

        licensing_score = licensing.get("license_clarity_score", 0)

        overall = citation_score * 0.4 + community_score * 0.4 + licensing_score * 0.2

        return int(round(overall))

    def _empty_citation_metrics(self) -> Dict:
        """Return empty citation metrics structure"""
        return {
            "citation_score": 0,
            "formal_citations": 0,
            "informal_mentions": 0,
            "dependent_packages": 0,
            "doi_resolutions": 0,
        }

    def _empty_community_metrics(self) -> Dict:
        """Return empty community metrics structure"""
        return {
            "total_contributors": 0,
            "active_contributors_30d": 0,
            "commit_frequency_per_month": 0,
            "avg_issue_response_days": 0,
            "avg_pr_merge_days": 0,
        }

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
