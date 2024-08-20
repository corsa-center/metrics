#!/usr/bin/env python3
"""
Generate Citation Metrics for CORSA Dashboard

This script reads the CORSA catalog and generates citation metrics
for each repository that can be integrated into the dashboard.

Usage:
    python scripts/generate_corsa_citations.py --catalog path/to/intRepo_Metadata.json

Environment Variables:
    GITHUB_TOKEN - GitHub personal access token
    SEMANTIC_SCHOLAR_KEY - Semantic Scholar API key (optional)
    OPENALEX_EMAIL - Email for OpenAlex polite pool (optional)
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from collectors.impact.citation import CitationMetricCollector

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("citation_collection.log"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


class CORSACitationGenerator:
    """Generate citation metrics for CORSA dashboard repositories"""

    def __init__(self, catalog_path: str, doi_mapping_path: str = None):
        """
        Initialize generator

        Args:
            catalog_path: Path to CORSA catalog JSON file
            doi_mapping_path: Optional path to DOI mapping file
        """
        self.catalog_path = Path(catalog_path)
        self.doi_mapping_path = Path(doi_mapping_path) if doi_mapping_path else None

        # Load catalog
        with open(self.catalog_path) as f:
            self.catalog = json.load(f)

        # Load DOI mappings if available
        self.doi_map = {}
        if self.doi_mapping_path and self.doi_mapping_path.exists():
            with open(self.doi_mapping_path) as f:
                self.doi_map = json.load(f)
            logger.info(f"Loaded {len(self.doi_map)} DOI mappings")
        else:
            logger.warning("No DOI mapping file found - will skip DOI-based metrics")

        # Configure citation collector
        config = {
            "api_credentials": {
                "github": {"token": os.environ.get("GITHUB_TOKEN", "")},
                "semantic_scholar": {
                    "api_key": os.environ.get("SEMANTIC_SCHOLAR_KEY", "")
                },
                "openalex": {"email": os.environ.get("OPENALEX_EMAIL", "")},
                "zenodo": {},
            },
            "metric_weights": {
                "impact_metrics": {
                    "citation": {
                        "sub_metrics": {
                            "formal_citations": 0.4,
                            "informal_mentions": 0.2,
                            "dependent_packages": 0.3,
                            "doi_resolutions": 0.1,
                        }
                    }
                }
            },
        }

        self.collector = CitationMetricCollector(config)
        logger.info("Citation collector initialized")

    async def collect_for_repository(
        self, repo_name: str, metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Collect citation metrics for a single repository

        Args:
            repo_name: Repository name (e.g., "numpy/numpy")
            metadata: Repository metadata from CORSA catalog

        Returns:
            Citation metrics dictionary
        """
        logger.info(f"Collecting metrics for {repo_name}")

        # Prepare package info
        package = {
            "name": metadata.get("description", repo_name.split("/")[-1]),
            "repo_url": f"https://github.com/{repo_name}",
            "doi": self.doi_map.get(repo_name),
        }

        try:
            result = await self.collector.collect(package)

            # Add metadata
            result["repository"] = repo_name
            result["collection_timestamp"] = datetime.utcnow().isoformat()

            logger.info(
                f"✓ {repo_name}: Score={result['score']}, Citations={result['sub_metrics']['formal_citations']['raw_value']}"
            )

            return result

        except Exception as e:
            logger.error(f"✗ Error collecting metrics for {repo_name}: {e}")
            return {
                "repository": repo_name,
                "error": str(e),
                "collection_timestamp": datetime.utcnow().isoformat(),
                "score": 0,
                "sub_metrics": {},
            }

    async def collect_all(
        self, limit: int = None, rate_limit_delay: float = 2.0
    ) -> Dict[str, Any]:
        """
        Collect citation metrics for all repositories in catalog

        Args:
            limit: Optional limit on number of repositories to process
            rate_limit_delay: Delay between API calls (seconds)

        Returns:
            Dictionary mapping repository names to citation metrics
        """
        results = {}
        repos = list(self.catalog.items())

        if limit:
            repos = repos[:limit]
            logger.info(f"Processing {limit} repositories (limited)")
        else:
            logger.info(f"Processing all {len(repos)} repositories")

        for i, (repo_name, metadata) in enumerate(repos, 1):
            logger.info(f"[{i}/{len(repos)}] Processing {repo_name}")

            result = await self.collect_for_repository(repo_name, metadata)
            results[repo_name] = result

            # Rate limiting
            if i < len(repos):
                await asyncio.sleep(rate_limit_delay)

        return results

    def save_results(self, results: Dict[str, Any], output_path: str):
        """
        Save results to JSON file in CORSA dashboard format

        Args:
            results: Citation metrics results
            output_path: Output file path
        """
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        with open(output_file, "w") as f:
            json.dump(results, f, indent=2)

        logger.info(f"✓ Results saved to {output_file}")

    def generate_summary(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate summary statistics

        Args:
            results: Citation metrics results

        Returns:
            Summary statistics dictionary
        """
        total_repos = len(results)
        successful = sum(1 for r in results.values() if "error" not in r)
        failed = total_repos - successful

        total_citations = sum(
            r.get("sub_metrics", {}).get("formal_citations", {}).get("raw_value", 0)
            for r in results.values()
        )

        avg_score = (
            sum(r.get("score", 0) for r in results.values()) / total_repos
            if total_repos > 0
            else 0
        )

        # Find top cited projects
        sorted_repos = sorted(
            [
                (
                    name,
                    r.get("sub_metrics", {})
                    .get("formal_citations", {})
                    .get("raw_value", 0),
                )
                for name, r in results.items()
            ],
            key=lambda x: x[1],
            reverse=True,
        )

        summary = {
            "total_repositories": total_repos,
            "successful_collections": successful,
            "failed_collections": failed,
            "total_citations": total_citations,
            "average_score": round(avg_score, 2),
            "top_cited": [
                {"repository": name, "citations": count}
                for name, count in sorted_repos[:10]
            ],
            "generated_at": datetime.utcnow().isoformat(),
        }

        return summary


async def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate citation metrics for CORSA dashboard"
    )
    parser.add_argument(
        "--catalog",
        default="catalog/intRepo_Metadata.json",
        help="Path to CORSA catalog JSON file",
    )
    parser.add_argument(
        "--doi-mapping",
        default="catalog/doi_mapping.json",
        help="Path to DOI mapping JSON file",
    )
    parser.add_argument(
        "--output",
        default="output/citationMetrics.json",
        help="Output path for citation metrics",
    )
    parser.add_argument(
        "--summary",
        default="output/citationSummary.json",
        help="Output path for summary statistics",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit number of repositories to process (for testing)",
    )
    parser.add_argument(
        "--rate-limit",
        type=float,
        default=2.0,
        help="Delay between API calls in seconds (default: 2.0)",
    )

    args = parser.parse_args()

    # Check for required environment variables
    if not os.environ.get("GITHUB_TOKEN"):
        logger.warning("GITHUB_TOKEN not set - some metrics may be incomplete")

    # Initialize generator
    generator = CORSACitationGenerator(args.catalog, args.doi_mapping)

    # Collect metrics
    print("\n" + "=" * 60)
    print("CORSA Citation Metrics Collection")
    print("=" * 60 + "\n")

    results = await generator.collect_all(
        limit=args.limit, rate_limit_delay=args.rate_limit
    )

    # Save results
    generator.save_results(results, args.output)

    # Generate and save summary
    summary = generator.generate_summary(results)
    generator.save_results(summary, args.summary)

    # Print summary
    print("\n" + "=" * 60)
    print("Collection Summary")
    print("=" * 60)
    print(f"Total Repositories:    {summary['total_repositories']}")
    print(f"Successful:            {summary['successful_collections']}")
    print(f"Failed:                {summary['failed_collections']}")
    print(f"Total Citations:       {summary['total_citations']:,}")
    print(f"Average Score:         {summary['average_score']:.2f}")
    print("\nTop 10 Most Cited Projects:")
    for i, item in enumerate(summary["top_cited"], 1):
        print(f"  {i:2d}. {item['repository']:40s} {item['citations']:6,} citations")
    print("=" * 60 + "\n")

    logger.info("Collection complete!")


if __name__ == "__main__":
    asyncio.run(main())
