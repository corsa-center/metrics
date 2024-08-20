"""
Zenodo API Integration

Provides access to DOI statistics and download metrics from Zenodo.
"""

import asyncio
import logging
from typing import Dict, Any, Optional
import httpx
from integrations.base import BaseAPIClient


class ZenodoClient(BaseAPIClient):
    """
    Client for Zenodo API

    Zenodo is a research data repository that provides DOIs for datasets and software.
    API Documentation: https://developers.zenodo.org/
    """

    BASE_URL = "https://zenodo.org/api"

    def __init__(self, credentials: Dict[str, Any]):
        """
        Initialize Zenodo client

        Args:
            credentials: Dict with optional 'access_token' for authenticated access
        """
        access_token = credentials.get("access_token")
        super().__init__(api_key=access_token, rate_limit=1000)

        self.headers = {}
        if access_token:
            self.headers["Authorization"] = f"Bearer {access_token}"

    async def get_doi_stats(self, doi: str) -> Dict[str, Any]:
        """
        Get statistics for a DOI (downloads, views, etc.)

        Args:
            doi: DOI identifier

        Returns:
            Dict with download and view counts
        """
        await self._check_rate_limit()

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Search for records by DOI
                search_url = f"{self.BASE_URL}/records"
                params = {"q": f'doi:"{doi}"', "size": 1}

                response = await client.get(
                    search_url, headers=self.headers, params=params
                )

                if response.status_code == 200:
                    data = response.json()
                    hits = data.get("hits", {}).get("hits", [])

                    if hits:
                        record = hits[0]
                        stats = record.get("stats", {})

                        return {
                            "downloads": stats.get("downloads", 0),
                            "views": stats.get("views", 0),
                            "unique_downloads": stats.get("unique_downloads", 0),
                            "unique_views": stats.get("unique_views", 0),
                            "version_downloads": stats.get("version_downloads", 0),
                            "version_views": stats.get("version_views", 0),
                            "record_id": record.get("id"),
                            "created": record.get("created"),
                        }
                    else:
                        self.logger.debug(f"No Zenodo record found for DOI: {doi}")
                        return {"downloads": 0, "views": 0}
                else:
                    self.logger.warning(f"Zenodo API error: {response.status_code}")
                    return {"downloads": 0, "views": 0}

        except httpx.TimeoutException:
            self.logger.error("Zenodo API timeout")
            return {"downloads": 0, "views": 0}
        except Exception as e:
            self.logger.error(f"Error fetching Zenodo stats: {e}")
            return {"downloads": 0, "views": 0}

    async def get_record_by_id(self, record_id: str) -> Dict[str, Any]:
        """
        Get full record details by Zenodo record ID

        Args:
            record_id: Zenodo record ID

        Returns:
            Dict with full record metadata
        """
        await self._check_rate_limit()

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                url = f"{self.BASE_URL}/records/{record_id}"

                response = await client.get(url, headers=self.headers)

                if response.status_code == 200:
                    data = response.json()

                    metadata = data.get("metadata", {})
                    stats = data.get("stats", {})

                    return {
                        "title": metadata.get("title"),
                        "doi": metadata.get("doi"),
                        "description": metadata.get("description"),
                        "publication_date": metadata.get("publication_date"),
                        "creators": metadata.get("creators", []),
                        "downloads": stats.get("downloads", 0),
                        "views": stats.get("views", 0),
                        "version": metadata.get("version"),
                        "files": data.get("files", []),
                    }
                else:
                    self.logger.warning(f"Record not found: {record_id}")
                    return {}

        except Exception as e:
            self.logger.error(f"Error fetching record: {e}")
            return {}

    async def search_records(self, query: str, limit: int = 10) -> list:
        """
        Search for records matching a query

        Args:
            query: Search query
            limit: Maximum number of results

        Returns:
            List of record dictionaries
        """
        await self._check_rate_limit()

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                url = f"{self.BASE_URL}/records"
                params = {
                    "q": query,
                    "size": limit,
                    "sort": "mostviewed",  # Sort by most viewed
                }

                response = await client.get(url, headers=self.headers, params=params)

                if response.status_code == 200:
                    data = response.json()
                    hits = data.get("hits", {}).get("hits", [])

                    records = []
                    for record in hits:
                        metadata = record.get("metadata", {})
                        stats = record.get("stats", {})

                        records.append(
                            {
                                "id": record.get("id"),
                                "title": metadata.get("title"),
                                "doi": metadata.get("doi"),
                                "downloads": stats.get("downloads", 0),
                                "views": stats.get("views", 0),
                                "created": record.get("created"),
                            }
                        )

                    return records
                else:
                    self.logger.warning(f"Search failed: {response.status_code}")
                    return []

        except Exception as e:
            self.logger.error(f"Error searching Zenodo: {e}")
            return []

    async def get_software_downloads(self, software_name: str) -> Dict[str, Any]:
        """
        Get download statistics for software packages on Zenodo

        Args:
            software_name: Name of the software

        Returns:
            Dict with total downloads and record count
        """
        await self._check_rate_limit()

        try:
            # Search for software records
            query = f'title:"{software_name}" AND type:software'
            records = await self.search_records(query, limit=100)

            total_downloads = sum(r.get("downloads", 0) for r in records)
            total_views = sum(r.get("views", 0) for r in records)

            return {
                "total_downloads": total_downloads,
                "total_views": total_views,
                "record_count": len(records),
                "top_records": records[:5],  # Return top 5
            }

        except Exception as e:
            self.logger.error(f"Error fetching software downloads: {e}")
            return {
                "total_downloads": 0,
                "total_views": 0,
                "record_count": 0,
                "top_records": [],
            }


# Example usage
async def main():
    """Example of using ZenodoClient"""

    logging.basicConfig(level=logging.INFO)

    # Initialize without token (public access)
    client = ZenodoClient({})

    # Test with a software DOI (example)
    print("Testing DOI stats...")
    stats = await client.get_doi_stats("10.5281/zenodo.1234567")  # Example DOI
    print(f"Downloads: {stats.get('downloads')}")
    print(f"Views: {stats.get('views')}")

    # Search for NumPy-related software
    print("\nSearching for NumPy software on Zenodo...")
    software_stats = await client.get_software_downloads("NumPy")
    print(f"Total downloads: {software_stats.get('total_downloads')}")
    print(f"Total records: {software_stats.get('record_count')}")


if __name__ == "__main__":
    asyncio.run(main())
