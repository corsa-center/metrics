"""
CASS Catalog Sync Script
Syncs CASS community software catalog with CORSA dashboard
"""
import json
import asyncio
import httpx
from bs4 import BeautifulSoup
from typing import Dict, List, Optional
import re


class CatalogSync:
    def __init__(self):
        self.base_url = "https://cass.community"
        self.catalog = []

    async def fetch_software_details(self, software_url: str) -> Dict:
        """Fetch detailed information from individual software page"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(software_url)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")

                # Extract GitHub URL
                github_url = None
                github_link = soup.find("a", href=re.compile(r"github\.com"))
                if github_link:
                    github_url = github_link.get("href")
                    # Normalize GitHub URL
                    if github_url:
                        # Remove trailing slashes, .git, etc.
                        github_url = github_url.rstrip("/")
                        if github_url.endswith(".git"):
                            github_url = github_url[:-4]
                        # Extract owner/repo format
                        match = re.search(r"github\.com/([^/]+/[^/]+)", github_url)
                        if match:
                            github_url = f"https://github.com/{match.group(1)}"

                # Extract categories/topics (if available)
                categories = []
                category_tags = soup.find_all("span", class_="badge") or soup.find_all(
                    "a", class_="tag"
                )
                for tag in category_tags:
                    cat_text = tag.get_text(strip=True)
                    if cat_text:
                        categories.append(cat_text)

                # Extract full description
                description = ""
                desc_elem = soup.find("meta", {"name": "description"})
                if desc_elem:
                    description = desc_elem.get("content", "")
                else:
                    # Try to find description in page content
                    content_div = soup.find("div", class_="content") or soup.find(
                        "main"
                    )
                    if content_div:
                        paragraphs = content_div.find_all("p")
                        if paragraphs:
                            description = paragraphs[0].get_text(strip=True)

                return {
                    "github_url": github_url,
                    "categories": categories,
                    "description": description,
                }

            except Exception as e:
                print(f"Error fetching {software_url}: {e}")
                return {"github_url": None, "categories": [], "description": ""}

    async def fetch_cass_catalog(self) -> List[Dict]:
        """Fetch complete CASS software catalog"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Try the main software page
            url = f"{self.base_url}/software-alpha/"
            print(f"Fetching CASS catalog from: {url}")

            try:
                response = await client.get(url)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")

                # Find all software links
                software_links = soup.find_all("a", href=re.compile(r"/software/"))

                print(f"Found {len(software_links)} software entries")

                catalog = []
                seen_names = set()

                for link in software_links:
                    name = link.get_text(strip=True)
                    href = link.get("href")

                    # Skip duplicates
                    if name in seen_names or not name:
                        continue
                    seen_names.add(name)

                    # Get full URL
                    software_url = (
                        f"{self.base_url}{href}" if href.startswith("/") else href
                    )

                    # Basic entry
                    entry = {"name": name, "cass_url": software_url}

                    catalog.append(entry)
                    print(f"  Added: {name}")

                return catalog

            except Exception as e:
                print(f"Error fetching catalog: {e}")
                return []

    async def enrich_catalog(self, catalog: List[Dict]) -> List[Dict]:
        """Enrich catalog entries with detailed information"""
        print(f"\nEnriching {len(catalog)} catalog entries...")

        enriched = []
        for i, entry in enumerate(catalog, 1):
            print(f"\n[{i}/{len(catalog)}] Processing {entry['name']}...")

            details = await self.fetch_software_details(entry["cass_url"])

            entry.update(
                {
                    "github_url": details["github_url"],
                    "categories": details["categories"],
                    "description": details["description"],
                }
            )

            enriched.append(entry)

            # Rate limit
            await asyncio.sleep(1)

        return enriched

    def convert_to_dashboard_format(self, catalog: List[Dict]) -> Dict:
        """Convert CASS catalog to dashboard input_lists.json format"""
        repos = []

        for entry in catalog:
            if entry.get("github_url"):
                # Extract owner/repo from GitHub URL
                match = re.search(r"github\.com/([^/]+/[^/]+)", entry["github_url"])
                if match:
                    repo_name = match.group(1)
                    repos.append(repo_name)
                    print(f"  Mapped: {entry['name']} -> {repo_name}")
                else:
                    print(
                        f"  Warning: Could not parse GitHub URL for {entry['name']}: {entry['github_url']}"
                    )
            else:
                print(f"  Warning: No GitHub URL for {entry['name']}")

        return {
            "repos": sorted(list(set(repos))),  # Remove duplicates and sort
            "extraRepos": [],
        }

    def merge_with_existing(self, new_repos: List[str], existing_file: str) -> Dict:
        """Merge new repos with existing input_lists.json"""
        try:
            with open(existing_file, "r") as f:
                existing = json.load(f)
        except FileNotFoundError:
            existing = {
                "https://github.com": {
                    "apiEnvKey": "GITHUB_API_TOKEN",
                    "extraRepos": [],
                    "memberOrgs": [],
                    "orgs": [],
                    "repoType": "github",
                    "repos": [],
                }
            }

        # Preserve structure - update GitHub repos section
        if "https://github.com" in existing:
            # Get existing GitHub repos (lowercase for comparison)
            existing_repos_lower = {
                r.lower() for r in existing["https://github.com"].get("repos", [])
            }
            existing_extra_lower = {
                r.lower() for r in existing["https://github.com"].get("extraRepos", [])
            }

            # Add new repos (case-insensitive deduplication)
            new_repos_to_add = []
            for repo in new_repos:
                if (
                    repo.lower() not in existing_repos_lower
                    and repo.lower() not in existing_extra_lower
                ):
                    new_repos_to_add.append(repo)

            # Combine all repos
            all_repos = (
                existing["https://github.com"].get("repos", []) + new_repos_to_add
            )

            # Update the structure
            existing["https://github.com"]["repos"] = sorted(
                list(set([r.lower() for r in all_repos]))
            )

        return existing

    async def run(self, output_catalog: str, dashboard_path: Optional[str] = None):
        """Main sync process"""
        print("=" * 60)
        print("CASS Catalog Sync")
        print("=" * 60)

        # Step 1: Fetch catalog
        catalog = await self.fetch_cass_catalog()

        # Step 2: Enrich with details
        enriched_catalog = await self.enrich_catalog(catalog)

        # Step 3: Save enriched catalog
        with open(output_catalog, "w") as f:
            json.dump(enriched_catalog, f, indent=2)
        print(f"\n✓ Saved enriched catalog to: {output_catalog}")

        # Step 4: Convert to dashboard format
        dashboard_data = self.convert_to_dashboard_format(enriched_catalog)

        # Step 5: If dashboard path provided, merge and update
        if dashboard_path:
            input_lists_file = f"{dashboard_path}/_explore/input_lists.json"
            merged = self.merge_with_existing(dashboard_data["repos"], input_lists_file)

            # Backup existing
            try:
                with open(input_lists_file, "r") as f:
                    existing_content = f.read()
                with open(f"{input_lists_file}.backup", "w") as f:
                    f.write(existing_content)
                print(f"✓ Backed up existing file to: {input_lists_file}.backup")
            except:
                pass

            # Write merged data
            with open(input_lists_file, "w") as f:
                json.dump(merged, f, indent=2)
            print(f"✓ Updated dashboard input_lists.json: {input_lists_file}")
            total_repos = len(merged.get("https://github.com", {}).get("repos", []))
            print(f"  Total GitHub repositories: {total_repos}")

        # Step 6: Print summary
        print("\n" + "=" * 60)
        print("Summary")
        print("=" * 60)
        print(f"Total CASS software entries: {len(enriched_catalog)}")
        print(
            f"Entries with GitHub URLs: {len([e for e in enriched_catalog if e.get('github_url')])}"
        )
        print(
            f"Entries without GitHub URLs: {len([e for e in enriched_catalog if not e.get('github_url')])}"
        )

        # List entries without GitHub
        missing_github = [
            e["name"] for e in enriched_catalog if not e.get("github_url")
        ]
        if missing_github:
            print(f"\nSoftware without GitHub URLs:")
            for name in missing_github:
                print(f"  - {name}")


async def main():
    sync = CatalogSync()
    await sync.run(
        output_catalog="/tmp/cass_software_catalog_enriched.json",
        dashboard_path="/home/brtnfld/work/dashboard",
    )


if __name__ == "__main__":
    asyncio.run(main())
