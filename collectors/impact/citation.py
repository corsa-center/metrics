"""
Citation Metric Collector for CASS Framework

Collects and aggregates citation-related metrics from multiple academic sources.
"""

import asyncio
import logging
from typing import Dict, Any, Optional
import yaml

from integrations.semantic_scholar import SemanticScholarClient
from integrations.openalex import OpenAlexClient
from integrations.zenodo import ZenodoClient
from integrations.github_api import GitHubClient


class CitationMetricCollector:
    """
    Collects citation metrics from academic sources

    Sub-metrics:
    - Formal citations (from papers, DOI resolutions)
    - Informal mentions (blog posts, documentation)
    - Dependent packages/projects
    - Download/usage statistics
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize citation collector

        Args:
            config: Configuration dict with API credentials and weights
        """
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)

        # Initialize API clients
        credentials = config.get("api_credentials", {})

        self.semantic_scholar = SemanticScholarClient(
            credentials.get("semantic_scholar", {})
        )
        self.openalex = OpenAlexClient(credentials.get("openalex", {}))
        self.zenodo = ZenodoClient(credentials.get("zenodo", {}))
        self.github = GitHubClient(credentials.get("github", {}))

        # Get metric weights from config
        weights = (
            config.get("metric_weights", {})
            .get("impact_metrics", {})
            .get("citation", {})
        )
        self.sub_metric_weights = weights.get(
            "sub_metrics",
            {
                "formal_citations": 0.4,
                "informal_mentions": 0.2,
                "dependent_packages": 0.3,
                "doi_resolutions": 0.1,
            },
        )

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        """
        Collect all citation metrics for a package

        Args:
            package: Dict with package metadata including 'name', 'doi', 'repo_url'

        Returns:
            Dict with citation metrics and sub-scores
        """
        self.logger.info(f"Collecting citation metrics for {package.get('name')}")

        # Collect all metrics concurrently
        results = await asyncio.gather(
            self._get_formal_citations(package),
            self._get_informal_mentions(package),
            self._get_dependent_packages(package),
            self._get_doi_resolutions(package),
            return_exceptions=True,
        )

        # Unpack results (handle any exceptions)
        formal_citations = results[0] if not isinstance(results[0], Exception) else 0
        informal_mentions = results[1] if not isinstance(results[1], Exception) else 0
        dependent_packages = results[2] if not isinstance(results[2], Exception) else 0
        doi_resolutions = results[3] if not isinstance(results[3], Exception) else 0

        # Log any exceptions
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                metric_names = [
                    "formal_citations",
                    "informal_mentions",
                    "dependent_packages",
                    "doi_resolutions",
                ]
                self.logger.error(f"Error collecting {metric_names[i]}: {result}")

        # Calculate normalized scores (0-100 scale)
        normalized_scores = {
            "formal_citations": self._normalize_citations(formal_citations),
            "informal_mentions": self._normalize_mentions(informal_mentions),
            "dependent_packages": self._normalize_dependents(dependent_packages),
            "doi_resolutions": self._normalize_dois(doi_resolutions),
        }

        # Calculate weighted total score
        total_score = sum(
            normalized_scores[metric] * weight
            for metric, weight in self.sub_metric_weights.items()
        )

        return {
            "metric_name": "citation",
            "score": round(total_score, 2),
            "sub_metrics": {
                "formal_citations": {
                    "raw_value": formal_citations,
                    "normalized_score": normalized_scores["formal_citations"],
                    "weight": self.sub_metric_weights["formal_citations"],
                },
                "informal_mentions": {
                    "raw_value": informal_mentions,
                    "normalized_score": normalized_scores["informal_mentions"],
                    "weight": self.sub_metric_weights["informal_mentions"],
                },
                "dependent_packages": {
                    "raw_value": dependent_packages,
                    "normalized_score": normalized_scores["dependent_packages"],
                    "weight": self.sub_metric_weights["dependent_packages"],
                },
                "doi_resolutions": {
                    "raw_value": doi_resolutions,
                    "normalized_score": normalized_scores["doi_resolutions"],
                    "weight": self.sub_metric_weights["doi_resolutions"],
                },
            },
            "metadata": {
                "timestamp": asyncio.get_event_loop().time(),
                "package_name": package.get("name"),
                "sources_checked": ["semantic_scholar", "openalex", "zenodo", "github"],
            },
        }

    async def _get_formal_citations(self, package: Dict[str, Any]) -> int:
        """Get formal academic citations from Semantic Scholar and OpenAlex"""
        doi = package.get("doi")
        name = package.get("name")

        if not doi and not name:
            self.logger.warning("No DOI or name provided for citation lookup")
            return 0

        # Query both services
        citations = []

        if doi:
            # Try OpenAlex first (often more comprehensive)
            try:
                openalex_data = await self.openalex.get_work_citations(doi)
                citations.append(openalex_data.get("cited_by_count", 0))
            except Exception as e:
                self.logger.warning(f"OpenAlex lookup failed: {e}")

        if doi or name:
            # Try Semantic Scholar
            try:
                ss_data = await self.semantic_scholar.get_citations(doi, name)
                citations.append(ss_data.get("citation_count", 0))
            except Exception as e:
                self.logger.warning(f"Semantic Scholar lookup failed: {e}")

        # Return the maximum count found (most authoritative source)
        return max(citations) if citations else 0

    async def _get_informal_mentions(self, package: Dict[str, Any]) -> int:
        """Get informal mentions from blogs, docs, etc."""
        name = package.get("name")

        if not name:
            return 0

        try:
            # Semantic Scholar can also track informal citations
            count = await self.semantic_scholar.get_mentions(
                name, include_informal=True
            )
            return count
        except Exception as e:
            self.logger.warning(f"Error fetching informal mentions: {e}")
            return 0

    async def _get_dependent_packages(self, package: Dict[str, Any]) -> int:
        """Get number of packages/repos that depend on this one"""
        repo_url = package.get("repo_url")

        if not repo_url:
            return 0

        try:
            # Check if repo has CITATION.cff or similar
            citation_file = await self.github.get_file_content(repo_url, "CITATION.cff")

            if citation_file:
                # Parse CITATION.cff to find dependent count
                # For now, just use GitHub's "Used by" count
                repo = await self.github.get_repository(repo_url)
                # Note: PyGithub doesn't directly expose "used by" count
                # We'd need to scrape the web UI or use GraphQL API
                # For now, use forks as a proxy
                stats = await self.github.get_repository_stats(repo_url)
                return stats.get("forks", 0)

            return 0

        except Exception as e:
            self.logger.warning(f"Error fetching dependent packages: {e}")
            return 0

    async def _get_doi_resolutions(self, package: Dict[str, Any]) -> int:
        """Get DOI resolution statistics from Zenodo"""
        doi = package.get("doi")

        if not doi:
            return 0

        try:
            stats = await self.zenodo.get_doi_stats(doi)
            # Combine views and downloads
            return stats.get("downloads", 0) + stats.get("views", 0)
        except Exception as e:
            self.logger.warning(f"Error fetching DOI stats: {e}")
            return 0

    # Normalization functions (0-100 scale)

    def _normalize_citations(self, count: int) -> float:
        """
        Normalize citation count to 0-100 scale

        Scale:
        - 0 citations = 0
        - 10 citations = 50
        - 100+ citations = 100
        """
        if count <= 0:
            return 0.0

        # Logarithmic scale for citations
        import math

        normalized = min(100, (math.log10(count + 1) / math.log10(101)) * 100)
        return round(normalized, 2)

    def _normalize_mentions(self, count: int) -> float:
        """
        Normalize informal mention count to 0-100 scale

        Scale:
        - 0 mentions = 0
        - 5 mentions = 50
        - 50+ mentions = 100
        """
        if count <= 0:
            return 0.0

        normalized = min(100, (count / 50) * 100)
        return round(normalized, 2)

    def _normalize_dependents(self, count: int) -> float:
        """
        Normalize dependent package count to 0-100 scale

        Scale:
        - 0 dependents = 0
        - 10 dependents = 50
        - 100+ dependents = 100
        """
        if count <= 0:
            return 0.0

        import math

        normalized = min(100, (math.log10(count + 1) / math.log10(101)) * 100)
        return round(normalized, 2)

    def _normalize_dois(self, count: int) -> float:
        """
        Normalize DOI resolution count to 0-100 scale

        Scale:
        - 0 resolutions = 0
        - 100 resolutions = 50
        - 1000+ resolutions = 100
        """
        if count <= 0:
            return 0.0

        import math

        normalized = min(100, (math.log10(count + 1) / math.log10(1001)) * 100)
        return round(normalized, 2)


# Example usage
async def main():
    """Example of using CitationMetricCollector"""

    # Setup logging
    logging.basicConfig(level=logging.INFO)

    # Configuration
    config = {
        "api_credentials": {
            "github": {"token": "your_github_token_here"},  # Replace with real token
            "semantic_scholar": {},
            "openalex": {},
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

    # Example package
    package = {
        "name": "numpy",
        "doi": "10.1038/s41586-020-2649-2",  # NumPy's Nature paper
        "repo_url": "https://github.com/numpy/numpy",
    }

    # Collect metrics
    collector = CitationMetricCollector(config)
    results = await collector.collect(package)

    # Display results
    import json

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
