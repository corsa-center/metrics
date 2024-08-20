"""
OpenAlex API Integration

Provides access to comprehensive academic metadata from OpenAlex.
"""

import asyncio
import logging
from typing import Dict, Any, Optional
import httpx
from integrations.base import BaseAPIClient


class OpenAlexClient(BaseAPIClient):
    """
    Client for OpenAlex API

    OpenAlex is a free, open catalog of scholarly papers, authors, institutions, and more.
    API Documentation: https://docs.openalex.org/
    """

    BASE_URL = "https://api.openalex.org"

    def __init__(self, credentials: Dict[str, Any]):
        """
        Initialize OpenAlex client

        Args:
            credentials: Dict with optional 'email' for polite pool (faster rate limits)
        """
        email = credentials.get("email")
        # Polite pool gets faster rate limits
        super().__init__(api_key=None, rate_limit=10000 if email else 1000)

        self.headers = {}
        if email:
            # Add email to headers for polite pool access
            self.headers["mailto"] = email
            self.logger.info(f"OpenAlex client using polite pool with email: {email}")

    async def get_work_citations(self, doi: str) -> Dict[str, Any]:
        """
        Get citation count and details for a work by DOI

        Args:
            doi: DOI of the work

        Returns:
            Dict with citation count and work details
        """
        await self._check_rate_limit()

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # OpenAlex uses DOI URLs as IDs
                doi_url = f"https://doi.org/{doi}"
                url = f"{self.BASE_URL}/works/{doi_url}"

                response = await client.get(url, headers=self.headers)

                if response.status_code == 200:
                    data = response.json()

                    return {
                        "cited_by_count": data.get("cited_by_count", 0),
                        "title": data.get("title"),
                        "publication_year": data.get("publication_year"),
                        "type": data.get("type"),
                        "open_access": data.get("open_access", {}).get("is_oa", False),
                        "openalex_id": data.get("id"),
                    }
                elif response.status_code == 404:
                    self.logger.debug(f"Work not found for DOI: {doi}")
                    return {"cited_by_count": 0}
                else:
                    self.logger.warning(f"OpenAlex API error: {response.status_code}")
                    return {"cited_by_count": 0}

        except httpx.TimeoutException:
            self.logger.error("OpenAlex API timeout")
            return {"cited_by_count": 0}
        except Exception as e:
            self.logger.error(f"Error fetching OpenAlex data: {e}")
            return {"cited_by_count": 0}

    async def search_works(self, query: str, limit: int = 10) -> list:
        """
        Search for works matching a query

        Args:
            query: Search query
            limit: Maximum number of results

        Returns:
            List of work dictionaries
        """
        await self._check_rate_limit()

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                url = f"{self.BASE_URL}/works"
                params = {"search": query, "per-page": limit}

                response = await client.get(url, headers=self.headers, params=params)

                if response.status_code == 200:
                    data = response.json()
                    results = data.get("results", [])

                    works = []
                    for work in results:
                        works.append(
                            {
                                "title": work.get("title"),
                                "doi": work.get("doi"),
                                "cited_by_count": work.get("cited_by_count", 0),
                                "publication_year": work.get("publication_year"),
                                "openalex_id": work.get("id"),
                            }
                        )

                    return works
                else:
                    self.logger.warning(f"Search failed: {response.status_code}")
                    return []

        except Exception as e:
            self.logger.error(f"Error searching OpenAlex: {e}")
            return []

    async def get_citations_over_time(self, doi: str) -> Dict[str, Any]:
        """
        Get citation counts by year for a work

        Args:
            doi: DOI of the work

        Returns:
            Dict with yearly citation counts
        """
        await self._check_rate_limit()

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                doi_url = f"https://doi.org/{doi}"
                url = f"{self.BASE_URL}/works/{doi_url}"

                response = await client.get(url, headers=self.headers)

                if response.status_code == 200:
                    data = response.json()

                    # Get counts by year from the work
                    counts_by_year = data.get("counts_by_year", [])

                    return {
                        "total_citations": data.get("cited_by_count", 0),
                        "by_year": counts_by_year,
                    }
                else:
                    return {"total_citations": 0, "by_year": []}

        except Exception as e:
            self.logger.error(f"Error fetching citation timeline: {e}")
            return {"total_citations": 0, "by_year": []}

    async def get_software_mentions(self, software_name: str) -> Dict[str, Any]:
        """
        Find academic papers that mention a software package

        Args:
            software_name: Name of the software

        Returns:
            Dict with mention count and sample works
        """
        await self._check_rate_limit()

        try:
            # Search for works mentioning the software
            works = await self.search_works(f'"{software_name}" software', limit=100)

            return {
                "mention_count": len(works),
                "sample_works": works[:10],  # Return top 10
            }

        except Exception as e:
            self.logger.error(f"Error searching for software mentions: {e}")
            return {"mention_count": 0, "sample_works": []}


# Example usage
async def main():
    """Example of using OpenAlexClient"""

    logging.basicConfig(level=logging.INFO)

    # Initialize with email for polite pool access (recommended)
    client = OpenAlexClient({"email": "your-email@example.com"})

    # Test with NumPy paper
    print("Testing with NumPy paper DOI...")
    result = await client.get_work_citations("10.1038/s41586-020-2649-2")
    print(f"Citations: {result.get('cited_by_count')}")
    print(f"Title: {result.get('title')}")
    print(f"Open Access: {result.get('open_access')}")

    # Get citation timeline
    print("\nGetting citation timeline...")
    timeline = await client.get_citations_over_time("10.1038/s41586-020-2649-2")
    print(f"Total citations: {timeline.get('total_citations')}")
    print(f"Recent years: {timeline.get('by_year', [])[:3]}")

    # Search for software mentions
    print("\nSearching for NumPy mentions...")
    mentions = await client.get_software_mentions("NumPy")
    print(f"Papers mentioning NumPy: {mentions.get('mention_count')}")


if __name__ == "__main__":
    asyncio.run(main())
