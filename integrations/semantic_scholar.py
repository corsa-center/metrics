"""
Semantic Scholar API Integration

Provides access to academic citation data from Semantic Scholar's API.
"""

import asyncio
import logging
from typing import Dict, Any, Optional
import httpx
from integrations.base import BaseAPIClient


class SemanticScholarClient(BaseAPIClient):
    """
    Client for Semantic Scholar Academic Graph API

    API Documentation: https://api.semanticscholar.org/api-docs/
    """

    BASE_URL = "https://api.semanticscholar.org/graph/v1"

    def __init__(self, credentials: Dict[str, Any]):
        """
        Initialize Semantic Scholar client

        Args:
            credentials: Dict with optional 'api_key' for higher rate limits
        """
        api_key = credentials.get("api_key")
        # Free tier: 100 requests/5min, with key: 5000 requests/5min
        rate_limit = 5000 if api_key else 100
        super().__init__(api_key, rate_limit=rate_limit)

        self.headers = {}
        if api_key:
            self.headers["x-api-key"] = api_key

    async def get_citations(
        self, doi: Optional[str] = None, title: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get citation count for a paper by DOI or title

        Args:
            doi: DOI of the paper
            title: Title of the paper (used if DOI not available)

        Returns:
            Dict with citation_count and paper details
        """
        await self._check_rate_limit()

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Search by DOI first (more accurate)
                if doi:
                    url = f"{self.BASE_URL}/paper/DOI:{doi}"
                    params = {"fields": "citationCount,title,year,authors"}

                    response = await client.get(
                        url, headers=self.headers, params=params
                    )

                    if response.status_code == 200:
                        data = response.json()
                        return {
                            "citation_count": data.get("citationCount", 0),
                            "title": data.get("title"),
                            "year": data.get("year"),
                            "paper_id": data.get("paperId"),
                        }
                    elif response.status_code == 404:
                        self.logger.debug(f"Paper not found for DOI: {doi}")
                    else:
                        self.logger.warning(
                            f"Semantic Scholar API error: {response.status_code}"
                        )

                # Fallback to title search
                if title:
                    search_url = f"{self.BASE_URL}/paper/search"
                    params = {
                        "query": title,
                        "fields": "citationCount,title,year",
                        "limit": 1,
                    }

                    response = await client.get(
                        search_url, headers=self.headers, params=params
                    )

                    if response.status_code == 200:
                        data = response.json()
                        papers = data.get("data", [])

                        if papers:
                            paper = papers[0]
                            return {
                                "citation_count": paper.get("citationCount", 0),
                                "title": paper.get("title"),
                                "year": paper.get("year"),
                                "paper_id": paper.get("paperId"),
                            }

                return {"citation_count": 0}

        except httpx.TimeoutException:
            self.logger.error("Semantic Scholar API timeout")
            return {"citation_count": 0}
        except Exception as e:
            self.logger.error(f"Error fetching Semantic Scholar data: {e}")
            return {"citation_count": 0}

    async def get_mentions(self, title: str, include_informal: bool = False) -> int:
        """
        Get mention count for a software/package

        Note: Semantic Scholar primarily tracks formal citations.
        For informal mentions, we search for papers mentioning the software.

        Args:
            title: Name/title of the software
            include_informal: Whether to include informal mentions

        Returns:
            Count of papers mentioning the software
        """
        await self._check_rate_limit()

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Search for papers mentioning the software
                search_url = f"{self.BASE_URL}/paper/search"
                params = {
                    "query": f'"{title}" software',
                    "fields": "citationCount",
                    "limit": 100,
                }

                response = await client.get(
                    search_url, headers=self.headers, params=params
                )

                if response.status_code == 200:
                    data = response.json()
                    papers = data.get("data", [])

                    # Count papers that mention this software
                    mention_count = len(papers)

                    self.logger.info(
                        f"Found {mention_count} papers mentioning '{title}'"
                    )
                    return mention_count
                else:
                    self.logger.warning(f"Search failed: {response.status_code}")
                    return 0

        except Exception as e:
            self.logger.error(f"Error searching for mentions: {e}")
            return 0

    async def get_paper_details(self, paper_id: str) -> Dict[str, Any]:
        """
        Get detailed information about a paper

        Args:
            paper_id: Semantic Scholar paper ID

        Returns:
            Dict with paper details
        """
        await self._check_rate_limit()

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                url = f"{self.BASE_URL}/paper/{paper_id}"
                params = {
                    "fields": "citationCount,influentialCitationCount,title,year,authors,venue,publicationTypes"
                }

                response = await client.get(url, headers=self.headers, params=params)

                if response.status_code == 200:
                    return response.json()
                else:
                    return {}

        except Exception as e:
            self.logger.error(f"Error fetching paper details: {e}")
            return {}


# Example usage
async def main():
    """Example of using SemanticScholarClient"""

    logging.basicConfig(level=logging.INFO)

    # Initialize without API key (rate limited)
    client = SemanticScholarClient({})

    # Test with NumPy paper
    print("Testing with NumPy paper DOI...")
    result = await client.get_citations(doi="10.1038/s41586-020-2649-2")
    print(f"Citations: {result.get('citation_count')}")
    print(f"Title: {result.get('title')}")

    # Test mentions
    print("\nSearching for papers mentioning NumPy...")
    mentions = await client.get_mentions("NumPy")
    print(f"Papers mentioning NumPy: {mentions}")


if __name__ == "__main__":
    asyncio.run(main())
