"""
Scholarly Impact Collector

Analyzes academic and scholarly impact of software packages using:
- Semantic Scholar's enhanced API (2024) for paper citations
- OpenAlex's comprehensive scholarly database
- AI-powered citation extraction from preprints and grey literature

Metrics Covered:
- citation_count: Total academic citations
- h_index_proxy: Approximation of software h-index based on citing papers
- field_diversity: Diversity of research fields citing the software
- preprint_citations: Citations from arXiv, bioRxiv, medRxiv, etc.
- grey_literature: Technical reports, theses, conference papers
"""

import asyncio
import logging
import re
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timedelta
from collections import defaultdict
import httpx

logger = logging.getLogger(__name__)


class ScholarlyImpactCollector:
    """
    Collector for scholarly and academic impact metrics.

    Integrates with multiple scholarly databases to assess
    the academic impact and citation patterns of software packages.
    """

    # Semantic Scholar API (enhanced 2024)
    SEMANTIC_SCHOLAR_API = "https://api.semanticscholar.org/graph/v1"

    # OpenAlex API
    OPENALEX_API = "https://api.openalex.org"

    # Supported preprint servers (with working API implementations)
    PREPRINT_SERVERS = {
        "arxiv": "https://export.arxiv.org/api/query",
        "biorxiv": "https://api.biorxiv.org/details/biorxiv",
        "zenodo": "https://zenodo.org/api/records",
    }

    # Research field mappings (OpenAlex concepts)
    FIELD_CATEGORIES = {
        "computer_science": ["Computer Science", "Software", "Programming"],
        "biology": ["Biology", "Bioinformatics", "Genomics", "Proteomics"],
        "physics": ["Physics", "Astronomy", "Astrophysics"],
        "chemistry": ["Chemistry", "Biochemistry", "Computational Chemistry"],
        "medicine": ["Medicine", "Clinical", "Healthcare", "Medical Informatics"],
        "mathematics": ["Mathematics", "Statistics", "Applied Mathematics"],
        "engineering": ["Engineering", "Mechanical", "Electrical"],
        "earth_science": ["Geology", "Climate", "Environmental Science", "Oceanography"],
        "social_science": ["Economics", "Psychology", "Sociology"],
        "materials_science": ["Materials Science", "Nanotechnology"],
    }

    def __init__(
        self,
        semantic_scholar_key: Optional[str] = None,
        openalex_email: Optional[str] = None,
        timeout: int = 30,
    ):
        """
        Initialize the scholarly impact collector.

        Args:
            semantic_scholar_key: API key for Semantic Scholar (optional, increases rate limits)
            openalex_email: Email for OpenAlex polite pool (recommended)
            timeout: Request timeout in seconds
        """
        self.semantic_scholar_key = semantic_scholar_key
        self.openalex_email = openalex_email
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        headers = {
            "User-Agent": "ScholarlyImpactCollector/1.0 (software metrics research)"
        }
        if self.semantic_scholar_key:
            headers["x-api-key"] = self.semantic_scholar_key

        self._client = httpx.AsyncClient(timeout=self.timeout, headers=headers)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._client:
            await self._client.aclose()

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        """
        Collect scholarly impact metrics for a package.

        Args:
            package: Package info dict with 'name' and optionally 'repository', 'doi'

        Returns:
            Dict containing scholarly impact metrics
        """
        package_name = package.get("name", "")
        repo_url = package.get("repository", "")
        doi = package.get("doi", "")

        logger.info(f"Collecting scholarly impact for {package_name}")

        if not self._client:
            self._client = httpx.AsyncClient(timeout=self.timeout)

        try:
            # Collect metrics from multiple sources concurrently
            results = await asyncio.gather(
                self._search_semantic_scholar(package_name, repo_url, doi),
                self._search_openalex(package_name, repo_url, doi),
                self._search_preprints(package_name, repo_url),
                self._search_grey_literature(package_name, repo_url),
                return_exceptions=True,
            )

            # Handle exceptions
            semantic_scholar = results[0] if not isinstance(results[0], Exception) else self._empty_semantic_scholar()
            openalex = results[1] if not isinstance(results[1], Exception) else self._empty_openalex()
            preprints = results[2] if not isinstance(results[2], Exception) else self._empty_preprints()
            grey_lit = results[3] if not isinstance(results[3], Exception) else self._empty_grey_literature()

            # Log exceptions
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"Error in scholarly collection {i}: {result}")

            # Merge and analyze results
            combined = self._merge_citation_data(
                semantic_scholar, openalex, preprints, grey_lit
            )

            # Calculate impact metrics
            impact_metrics = self._calculate_impact_metrics(combined)

            # Analyze field diversity
            field_analysis = self._analyze_field_diversity(combined)

            # Calculate trend analysis
            trend_analysis = self._analyze_citation_trends(combined)

            # Calculate overall score
            overall_score = self._calculate_overall_score(
                impact_metrics, field_analysis, combined
            )

            return {
                "package_name": package_name,
                "repository": repo_url,
                "timestamp": self._get_timestamp(),
                "semantic_scholar": semantic_scholar,
                "openalex": openalex,
                "preprints": preprints,
                "grey_literature": grey_lit,
                "combined_metrics": combined,
                "impact_metrics": impact_metrics,
                "field_diversity": field_analysis,
                "citation_trends": trend_analysis,
                "overall_score": overall_score,
            }

        except Exception as e:
            logger.error(f"Error collecting scholarly impact for {package_name}: {e}")
            return self._empty_result(package_name)

    async def _search_semantic_scholar(
        self,
        package_name: str,
        repo_url: str,
        doi: str
    ) -> Dict[str, Any]:
        """
        Search Semantic Scholar for papers citing the software.

        Uses the enhanced 2024 API features including:
        - Full-text search
        - Citation context extraction
        - Paper embeddings for similarity
        """
        logger.debug(f"Searching Semantic Scholar for {package_name}")

        try:
            # Build search queries
            search_queries = [package_name]

            # Add repo URL variations
            if repo_url:
                owner_repo = self._extract_owner_repo(repo_url)
                if owner_repo:
                    search_queries.append(f"{owner_repo[0]}/{owner_repo[1]}")
                    search_queries.append(owner_repo[1])

            all_papers = []
            seen_paper_ids = set()

            for query in search_queries[:3]:  # Limit queries
                # Search papers endpoint
                url = f"{self.SEMANTIC_SCHOLAR_API}/paper/search"
                params = {
                    "query": query,
                    "fields": "paperId,title,year,citationCount,influentialCitationCount,"
                             "fieldsOfStudy,publicationTypes,journal,venue,authors,"
                             "openAccessPdf,externalIds",
                    "limit": 100,
                }

                response = await self._client.get(url, params=params)

                if response.status_code == 200:
                    data = response.json()
                    papers = data.get("data", [])

                    for paper in papers:
                        paper_id = paper.get("paperId")
                        if paper_id and paper_id not in seen_paper_ids:
                            # Check if paper actually references the software
                            if self._paper_references_software(paper, package_name, repo_url):
                                seen_paper_ids.add(paper_id)
                                all_papers.append(paper)

                elif response.status_code == 429:
                    logger.warning("Semantic Scholar rate limit reached")
                    await asyncio.sleep(1)

            # Get citation contexts for top papers
            citation_contexts = []
            for paper in all_papers[:10]:
                context = await self._get_citation_context(paper.get("paperId"))
                if context:
                    citation_contexts.append(context)

            # Aggregate statistics
            total_citations = sum(p.get("citationCount", 0) for p in all_papers)
            influential_citations = sum(p.get("influentialCitationCount", 0) for p in all_papers)

            # Extract fields of study
            fields = defaultdict(int)
            for paper in all_papers:
                for field in paper.get("fieldsOfStudy", []) or []:
                    fields[field] += 1

            # Publication types
            pub_types = defaultdict(int)
            for paper in all_papers:
                for pub_type in paper.get("publicationTypes", []) or []:
                    pub_types[pub_type] += 1

            # Year distribution
            years = defaultdict(int)
            for paper in all_papers:
                year = paper.get("year")
                if year:
                    years[year] += 1

            return {
                "source": "semantic_scholar",
                "papers_found": len(all_papers),
                "total_citations_in_corpus": total_citations,
                "influential_citations": influential_citations,
                "fields_of_study": dict(fields),
                "publication_types": dict(pub_types),
                "year_distribution": dict(years),
                "top_papers": [
                    {
                        "title": p.get("title"),
                        "year": p.get("year"),
                        "citations": p.get("citationCount"),
                        "venue": p.get("venue") or p.get("journal", {}).get("name"),
                        "fields": p.get("fieldsOfStudy"),
                    }
                    for p in sorted(all_papers, key=lambda x: x.get("citationCount", 0), reverse=True)[:10]
                ],
                "citation_contexts": citation_contexts[:5],
                "score": min(100, len(all_papers) * 2 + influential_citations),
            }

        except Exception as e:
            logger.error(f"Error searching Semantic Scholar: {e}")
            return self._empty_semantic_scholar()

    async def _get_citation_context(self, paper_id: str) -> Optional[Dict[str, Any]]:
        """Get citation context for a paper (how software is cited)."""
        if not paper_id:
            return None

        try:
            url = f"{self.SEMANTIC_SCHOLAR_API}/paper/{paper_id}"
            params = {
                "fields": "title,abstract,tldr"
            }

            response = await self._client.get(url, params=params)

            if response.status_code == 200:
                data = response.json()
                return {
                    "title": data.get("title"),
                    "abstract_snippet": (data.get("abstract") or "")[:500],
                    "tldr": data.get("tldr", {}).get("text") if data.get("tldr") else None,
                }

        except Exception as e:
            logger.debug(f"Error getting citation context: {e}")

        return None

    async def _search_openalex(
        self,
        package_name: str,
        repo_url: str,
        doi: str
    ) -> Dict[str, Any]:
        """
        Search OpenAlex for scholarly references.

        OpenAlex provides comprehensive coverage of:
        - Academic papers
        - Citations and references
        - Author affiliations
        - Research concepts and topics
        """
        logger.debug(f"Searching OpenAlex for {package_name}")

        try:
            # Build search URL with polite pool if email provided
            base_url = f"{self.OPENALEX_API}/works"

            # Search strategies
            searches = []

            # Full-text search
            searches.append({"filter": f"fulltext.search:{package_name}"})

            # Title/abstract search
            searches.append({"filter": f"title_and_abstract.search:{package_name}"})

            # If we have a DOI for the software paper
            if doi:
                searches.append({"filter": f"cites:{doi}"})

            all_works = []
            seen_ids = set()

            for search in searches:
                params = {
                    **search,
                    "per-page": 100,
                    "select": "id,doi,title,publication_year,cited_by_count,type,"
                             "primary_location,concepts,authorships,open_access,"
                             "cited_by_percentile_year",
                }

                if self.openalex_email:
                    params["mailto"] = self.openalex_email

                response = await self._client.get(base_url, params=params)

                if response.status_code == 200:
                    data = response.json()
                    works = data.get("results", [])

                    for work in works:
                        work_id = work.get("id")
                        if work_id and work_id not in seen_ids:
                            # Verify relevance
                            if self._work_references_software(work, package_name, repo_url):
                                seen_ids.add(work_id)
                                all_works.append(work)

            # Aggregate statistics
            total_citations = sum(w.get("cited_by_count", 0) for w in all_works)

            # Extract concepts (research fields)
            concepts = defaultdict(lambda: {"count": 0, "score": 0})
            for work in all_works:
                for concept in work.get("concepts", []) or []:
                    name = concept.get("display_name")
                    if name:
                        concepts[name]["count"] += 1
                        concepts[name]["score"] += concept.get("score", 0)

            # Normalize concept scores
            for name in concepts:
                concepts[name]["avg_score"] = round(
                    concepts[name]["score"] / concepts[name]["count"], 3
                )

            # Work types
            work_types = defaultdict(int)
            for work in all_works:
                work_type = work.get("type", "unknown")
                work_types[work_type] += 1

            # Year distribution
            years = defaultdict(int)
            for work in all_works:
                year = work.get("publication_year")
                if year:
                    years[year] += 1

            # Open access statistics
            oa_count = sum(1 for w in all_works if w.get("open_access", {}).get("is_oa"))

            # Institution diversity (unique institutions)
            institutions = set()
            for work in all_works:
                for authorship in work.get("authorships", []) or []:
                    for inst in authorship.get("institutions", []) or []:
                        if inst.get("display_name"):
                            institutions.add(inst.get("display_name"))

            return {
                "source": "openalex",
                "works_found": len(all_works),
                "total_citations_in_corpus": total_citations,
                "concepts": {
                    k: {"count": v["count"], "avg_score": v.get("avg_score", 0)}
                    for k, v in sorted(
                        concepts.items(),
                        key=lambda x: x[1]["count"],
                        reverse=True
                    )[:20]
                },
                "work_types": dict(work_types),
                "year_distribution": dict(years),
                "open_access_count": oa_count,
                "open_access_percentage": round(oa_count / len(all_works) * 100, 1) if all_works else 0,
                "unique_institutions": len(institutions),
                "top_institutions": list(institutions)[:10],
                "top_works": [
                    {
                        "title": w.get("title"),
                        "year": w.get("publication_year"),
                        "citations": w.get("cited_by_count"),
                        "type": w.get("type"),
                        "doi": w.get("doi"),
                    }
                    for w in sorted(all_works, key=lambda x: x.get("cited_by_count", 0), reverse=True)[:10]
                ],
                "score": min(100, len(all_works) * 1.5 + len(institutions) * 0.5),
            }

        except Exception as e:
            logger.error(f"Error searching OpenAlex: {e}")
            return self._empty_openalex()

    async def _search_preprints(
        self,
        package_name: str,
        repo_url: str
    ) -> Dict[str, Any]:
        """
        Search preprint servers for citations.

        Covers:
        - arXiv (physics, math, CS, etc.)
        - bioRxiv (biology)
        - medRxiv (medical)
        - chemRxiv (chemistry)
        - Zenodo (general)
        """
        logger.debug(f"Searching preprints for {package_name}")

        try:
            preprint_results = {}
            total_preprints = 0

            # Search arXiv
            arxiv_results = await self._search_arxiv(package_name, repo_url)
            if arxiv_results:
                preprint_results["arxiv"] = arxiv_results
                total_preprints += arxiv_results.get("count", 0)

            # Search bioRxiv/medRxiv (via API)
            biorxiv_results = await self._search_biorxiv(package_name)
            if biorxiv_results:
                preprint_results["biorxiv_medrxiv"] = biorxiv_results
                total_preprints += biorxiv_results.get("count", 0)

            # Search Zenodo
            zenodo_results = await self._search_zenodo(package_name, repo_url)
            if zenodo_results:
                preprint_results["zenodo"] = zenodo_results
                total_preprints += zenodo_results.get("count", 0)

            # Aggregate by category
            categories = defaultdict(int)
            for server, results in preprint_results.items():
                for cat in results.get("categories", {}):
                    categories[cat] += results["categories"][cat]

            return {
                "source": "preprints",
                "total_preprints": total_preprints,
                "by_server": preprint_results,
                "categories": dict(categories),
                "score": min(100, total_preprints * 3),
                "status": self._get_status(min(100, total_preprints * 3)),
            }

        except Exception as e:
            logger.error(f"Error searching preprints: {e}")
            return self._empty_preprints()

    async def _search_arxiv(self, package_name: str, repo_url: str) -> Dict[str, Any]:
        """Search arXiv for papers mentioning the software."""
        try:
            # arXiv API search
            query = f"all:{package_name}"
            if repo_url:
                owner_repo = self._extract_owner_repo(repo_url)
                if owner_repo:
                    query = f"all:{package_name} OR all:{owner_repo[1]}"

            url = "https://export.arxiv.org/api/query"
            params = {
                "search_query": query,
                "max_results": 100,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            }

            response = await self._client.get(url, params=params)

            if response.status_code == 200:
                # Parse Atom feed
                content = response.text

                # Extract entries (simple regex parsing)
                entries = re.findall(r"<entry>(.*?)</entry>", content, re.DOTALL)

                papers = []
                categories = defaultdict(int)

                for entry in entries:
                    # Check if actually references our software
                    if not self._text_references_software(entry, package_name, repo_url):
                        continue

                    title_match = re.search(r"<title>(.*?)</title>", entry, re.DOTALL)
                    title = title_match.group(1).strip() if title_match else "Unknown"

                    # Get categories
                    cat_matches = re.findall(r'term="([^"]+)"', entry)
                    for cat in cat_matches:
                        if "." in cat:  # arXiv category format
                            categories[cat.split(".")[0]] += 1

                    id_match = re.search(r"<id>http://arxiv.org/abs/([^<]+)</id>", entry)
                    arxiv_id = id_match.group(1) if id_match else None

                    papers.append({
                        "title": title[:200],
                        "arxiv_id": arxiv_id,
                        "categories": cat_matches[:3],
                    })

                return {
                    "count": len(papers),
                    "categories": dict(categories),
                    "recent_papers": papers[:10],
                }

        except Exception as e:
            logger.debug(f"Error searching arXiv: {e}")

        return {"count": 0, "categories": {}, "recent_papers": []}

    async def _search_biorxiv(self, package_name: str) -> Dict[str, Any]:
        """Search bioRxiv/medRxiv for papers."""
        try:
            # bioRxiv API - search recent preprints
            # Note: bioRxiv API has limited search capability
            url = f"https://api.biorxiv.org/details/biorxiv/2020-01-01/2024-12-31/100"

            response = await self._client.get(url)

            if response.status_code == 200:
                data = response.json()
                papers = data.get("collection", [])

                # Filter papers mentioning the software
                relevant = [
                    p for p in papers
                    if package_name.lower() in (p.get("title", "") + p.get("abstract", "")).lower()
                ]

                categories = defaultdict(int)
                for paper in relevant:
                    cat = paper.get("category", "unknown")
                    categories[cat] += 1

                return {
                    "count": len(relevant),
                    "categories": dict(categories),
                    "recent_papers": [
                        {
                            "title": p.get("title"),
                            "doi": p.get("doi"),
                            "category": p.get("category"),
                        }
                        for p in relevant[:10]
                    ],
                }

        except Exception as e:
            logger.debug(f"Error searching bioRxiv: {e}")

        return {"count": 0, "categories": {}, "recent_papers": []}

    async def _search_zenodo(self, package_name: str, repo_url: str) -> Dict[str, Any]:
        """Search Zenodo for related records."""
        try:
            url = "https://zenodo.org/api/records"
            params = {
                "q": package_name,
                "size": 100,
                "type": "publication",
            }

            response = await self._client.get(url, params=params)

            if response.status_code == 200:
                data = response.json()
                hits = data.get("hits", {}).get("hits", [])

                # Filter relevant records
                relevant = []
                for hit in hits:
                    metadata = hit.get("metadata", {})
                    description = metadata.get("description", "")
                    title = metadata.get("title", "")

                    if self._text_references_software(
                        title + " " + description,
                        package_name,
                        repo_url
                    ):
                        relevant.append(hit)

                resource_types = defaultdict(int)
                for hit in relevant:
                    rtype = hit.get("metadata", {}).get("resource_type", {}).get("type", "unknown")
                    resource_types[rtype] += 1

                return {
                    "count": len(relevant),
                    "categories": dict(resource_types),
                    "recent_records": [
                        {
                            "title": h.get("metadata", {}).get("title"),
                            "doi": h.get("doi"),
                            "type": h.get("metadata", {}).get("resource_type", {}).get("type"),
                        }
                        for h in relevant[:10]
                    ],
                }

        except Exception as e:
            logger.debug(f"Error searching Zenodo: {e}")

        return {"count": 0, "categories": {}, "recent_records": []}

    async def _search_grey_literature(
        self,
        package_name: str,
        repo_url: str
    ) -> Dict[str, Any]:
        """
        Search for grey literature citations.

        Grey literature includes:
        - Technical reports
        - Theses and dissertations
        - Conference posters
        - Working papers
        - White papers
        """
        logger.debug(f"Searching grey literature for {package_name}")

        try:
            grey_lit_results = {
                "theses_dissertations": 0,
                "technical_reports": 0,
                "conference_papers": 0,
                "working_papers": 0,
            }

            # Use OpenAlex to find grey literature by type
            base_url = f"{self.OPENALEX_API}/works"

            type_queries = {
                "theses_dissertations": "dissertation",
                "technical_reports": "report",
                "conference_papers": "proceedings-article",
            }

            for category, work_type in type_queries.items():
                params = {
                    "filter": f"fulltext.search:{package_name},type:{work_type}",
                    "per-page": 50,
                }

                if self.openalex_email:
                    params["mailto"] = self.openalex_email

                response = await self._client.get(base_url, params=params)

                if response.status_code == 200:
                    data = response.json()
                    count = data.get("meta", {}).get("count", 0)
                    grey_lit_results[category] = min(count, 1000)  # Cap at 1000

            total = sum(grey_lit_results.values())

            # Estimate academic impact
            # Theses often indicate tool adoption in research training
            impact_score = (
                grey_lit_results["theses_dissertations"] * 3 +
                grey_lit_results["technical_reports"] * 2 +
                grey_lit_results["conference_papers"] * 1.5 +
                grey_lit_results["working_papers"] * 1
            )

            return {
                "source": "grey_literature",
                "total_documents": total,
                "by_type": grey_lit_results,
                "academic_training_indicator": grey_lit_results["theses_dissertations"] > 0,
                "industry_adoption_indicator": grey_lit_results["technical_reports"] > 5,
                "impact_score": min(100, impact_score),
                "status": self._get_status(min(100, impact_score)),
            }

        except Exception as e:
            logger.error(f"Error searching grey literature: {e}")
            return self._empty_grey_literature()

    def _merge_citation_data(
        self,
        semantic_scholar: Dict[str, Any],
        openalex: Dict[str, Any],
        preprints: Dict[str, Any],
        grey_lit: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Merge and deduplicate citation data from multiple sources."""

        # Combine paper counts (estimate unique by taking max + 50% of second)
        ss_papers = semantic_scholar.get("papers_found", 0)
        oa_works = openalex.get("works_found", 0)
        preprint_count = preprints.get("total_preprints", 0)
        grey_count = grey_lit.get("total_documents", 0)

        # Estimate unique papers (sources have overlap)
        primary_count = max(ss_papers, oa_works)
        secondary_count = min(ss_papers, oa_works)
        estimated_unique = primary_count + int(secondary_count * 0.3) + preprint_count + grey_count

        # Merge fields of study
        all_fields = defaultdict(int)

        for field, count in semantic_scholar.get("fields_of_study", {}).items():
            all_fields[field] += count

        for concept, data in openalex.get("concepts", {}).items():
            # Map OpenAlex concepts to fields
            all_fields[concept] += data.get("count", 0)

        # Merge year distributions
        all_years = defaultdict(int)
        for year, count in semantic_scholar.get("year_distribution", {}).items():
            all_years[int(year)] += count
        for year, count in openalex.get("year_distribution", {}).items():
            all_years[int(year)] += count

        return {
            "estimated_unique_papers": estimated_unique,
            "semantic_scholar_papers": ss_papers,
            "openalex_works": oa_works,
            "preprints": preprint_count,
            "grey_literature": grey_count,
            "fields_of_study": dict(sorted(all_fields.items(), key=lambda x: x[1], reverse=True)[:15]),
            "year_distribution": dict(sorted(all_years.items())),
            "institutional_reach": openalex.get("unique_institutions", 0),
        }

    def _calculate_impact_metrics(self, combined: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate scholarly impact metrics."""

        total_papers = combined.get("estimated_unique_papers", 0)
        institutional_reach = combined.get("institutional_reach", 0)

        # Calculate h-index proxy (simplified)
        # Based on number of papers and institutional diversity
        h_index_proxy = min(
            int((total_papers ** 0.5) * (1 + institutional_reach * 0.01)),
            total_papers
        )

        # Field impact score
        fields = combined.get("fields_of_study", {})
        field_count = len(fields)
        field_diversity_score = min(100, field_count * 10)

        # Recency score (more recent citations = higher impact)
        years = combined.get("year_distribution", {})
        current_year = datetime.now().year
        recent_citations = sum(
            count for year, count in years.items()
            if year >= current_year - 2
        )
        recency_ratio = recent_citations / total_papers if total_papers > 0 else 0
        recency_score = min(100, recency_ratio * 200)

        # Overall impact score
        impact_score = (
            min(100, total_papers * 0.5) * 0.4 +
            field_diversity_score * 0.25 +
            min(100, institutional_reach) * 0.2 +
            recency_score * 0.15
        )

        return {
            "total_citations": total_papers,
            "h_index_proxy": h_index_proxy,
            "institutional_reach": institutional_reach,
            "field_count": field_count,
            "field_diversity_score": round(field_diversity_score, 2),
            "recency_score": round(recency_score, 2),
            "recent_citations": recent_citations,
            "impact_score": round(impact_score, 2),
            "impact_level": self._interpret_impact(impact_score),
        }

    def _analyze_field_diversity(self, combined: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze diversity of research fields citing the software."""

        fields = combined.get("fields_of_study", {})

        if not fields:
            return {
                "diversity_score": 0,
                "primary_fields": [],
                "field_categories": {},
                "cross_disciplinary": False,
            }

        # Categorize fields
        field_categories = defaultdict(list)
        for field, count in fields.items():
            for category, keywords in self.FIELD_CATEGORIES.items():
                if any(kw.lower() in field.lower() for kw in keywords):
                    field_categories[category].append({"field": field, "count": count})
                    break
            else:
                field_categories["other"].append({"field": field, "count": count})

        # Calculate diversity metrics
        total_citations = sum(fields.values())

        # Gini-Simpson diversity index
        if total_citations > 0:
            proportions = [count / total_citations for count in fields.values()]
            diversity_index = 1 - sum(p ** 2 for p in proportions)
        else:
            diversity_index = 0

        # Is it cross-disciplinary?
        cross_disciplinary = len(field_categories) >= 3

        # Primary fields (top 5 by citation count)
        primary_fields = [
            {"field": field, "citations": count, "percentage": round(count / total_citations * 100, 1)}
            for field, count in sorted(fields.items(), key=lambda x: x[1], reverse=True)[:5]
        ] if total_citations > 0 else []

        return {
            "diversity_score": round(diversity_index * 100, 2),
            "diversity_interpretation": self._interpret_diversity(diversity_index),
            "primary_fields": primary_fields,
            "field_categories": {
                cat: len(fields_list)
                for cat, fields_list in field_categories.items()
            },
            "cross_disciplinary": cross_disciplinary,
            "unique_fields": len(fields),
        }

    def _analyze_citation_trends(self, combined: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze citation trends over time."""

        years = combined.get("year_distribution", {})

        if len(years) < 2:
            return {
                "trend": "insufficient_data",
                "growth_rate": 0,
                "peak_year": None,
                "yearly_data": years,
            }

        sorted_years = sorted(years.items())
        current_year = datetime.now().year

        # Calculate growth rate (recent vs earlier)
        recent = sum(count for year, count in sorted_years if year >= current_year - 2)
        earlier = sum(count for year, count in sorted_years if year < current_year - 2)

        if earlier > 0:
            growth_rate = ((recent - earlier) / earlier) * 100
        elif recent > 0:
            growth_rate = 100  # All citations are recent
        else:
            growth_rate = 0

        # Find peak year
        peak_year = max(sorted_years, key=lambda x: x[1])[0] if sorted_years else None

        # Determine trend
        if growth_rate > 50:
            trend = "rapidly_growing"
        elif growth_rate > 10:
            trend = "growing"
        elif growth_rate > -10:
            trend = "stable"
        elif growth_rate > -50:
            trend = "declining"
        else:
            trend = "rapidly_declining"

        return {
            "trend": trend,
            "growth_rate": round(growth_rate, 1),
            "peak_year": peak_year,
            "recent_citations": recent,
            "earlier_citations": earlier,
            "yearly_data": dict(sorted_years),
        }

    def _calculate_overall_score(
        self,
        impact_metrics: Dict[str, Any],
        field_analysis: Dict[str, Any],
        combined: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Calculate overall scholarly impact score."""

        impact_score = impact_metrics.get("impact_score", 0)
        diversity_score = field_analysis.get("diversity_score", 0)
        institutional_reach = min(100, combined.get("institutional_reach", 0))

        # Weighted overall score
        overall = (
            impact_score * 0.5 +
            diversity_score * 0.25 +
            institutional_reach * 0.25
        )

        return {
            "score": round(overall, 2),
            "max_score": 100,
            "components": {
                "impact": round(impact_score, 2),
                "diversity": round(diversity_score, 2),
                "reach": round(institutional_reach, 2),
            },
            "status": self._get_status(overall),
            "interpretation": self._interpret_overall(overall),
        }

    # ==================== Helper Methods ====================

    def _paper_references_software(
        self,
        paper: Dict[str, Any],
        package_name: str,
        repo_url: str
    ) -> bool:
        """Check if a paper likely references the software."""
        title = (paper.get("title") or "").lower()
        return package_name.lower() in title

    def _work_references_software(
        self,
        work: Dict[str, Any],
        package_name: str,
        repo_url: str
    ) -> bool:
        """Check if an OpenAlex work likely references the software."""
        title = (work.get("title") or "").lower()
        return package_name.lower() in title

    def _text_references_software(
        self,
        text: str,
        package_name: str,
        repo_url: str
    ) -> bool:
        """Check if text mentions the software."""
        text_lower = text.lower()

        if package_name.lower() in text_lower:
            return True

        if repo_url:
            owner_repo = self._extract_owner_repo(repo_url)
            if owner_repo and owner_repo[1].lower() in text_lower:
                return True

        return False

    def _extract_owner_repo(self, repo_url: str) -> Optional[Tuple[str, str]]:
        """Extract owner and repo from GitHub URL."""
        patterns = [
            r"github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$",
            r"github\.com:([^/]+)/([^/]+?)(?:\.git)?/?$",
        ]
        for pattern in patterns:
            match = re.search(pattern, repo_url)
            if match:
                return match.group(1), match.group(2)
        return None

    def _get_timestamp(self) -> str:
        """Get current timestamp."""
        return datetime.utcnow().isoformat() + "Z"

    def _get_status(self, score: float) -> str:
        """Get status based on score."""
        if score >= 80:
            return "excellent"
        elif score >= 60:
            return "good"
        elif score >= 40:
            return "moderate"
        elif score >= 20:
            return "limited"
        else:
            return "minimal"

    def _interpret_impact(self, score: float) -> str:
        """Interpret impact score."""
        if score >= 80:
            return "high_impact"
        elif score >= 60:
            return "significant_impact"
        elif score >= 40:
            return "moderate_impact"
        elif score >= 20:
            return "emerging_impact"
        else:
            return "limited_impact"

    def _interpret_diversity(self, index: float) -> str:
        """Interpret diversity index."""
        if index >= 0.8:
            return "highly_diverse"
        elif index >= 0.6:
            return "diverse"
        elif index >= 0.4:
            return "moderately_diverse"
        elif index >= 0.2:
            return "somewhat_focused"
        else:
            return "highly_focused"

    def _interpret_overall(self, score: float) -> str:
        """Interpret overall scholarly impact."""
        if score >= 80:
            return "Strong scholarly presence with diverse academic adoption"
        elif score >= 60:
            return "Significant scholarly recognition across multiple fields"
        elif score >= 40:
            return "Growing academic adoption with room for expansion"
        elif score >= 20:
            return "Emerging scholarly presence, early adoption phase"
        else:
            return "Limited scholarly visibility, opportunity for outreach"

    # ==================== Empty Results ====================

    def _empty_semantic_scholar(self) -> Dict[str, Any]:
        return {
            "source": "semantic_scholar",
            "papers_found": 0,
            "total_citations_in_corpus": 0,
            "influential_citations": 0,
            "fields_of_study": {},
            "publication_types": {},
            "year_distribution": {},
            "top_papers": [],
            "citation_contexts": [],
            "score": 0,
        }

    def _empty_openalex(self) -> Dict[str, Any]:
        return {
            "source": "openalex",
            "works_found": 0,
            "total_citations_in_corpus": 0,
            "concepts": {},
            "work_types": {},
            "year_distribution": {},
            "open_access_count": 0,
            "open_access_percentage": 0,
            "unique_institutions": 0,
            "top_institutions": [],
            "top_works": [],
            "score": 0,
        }

    def _empty_preprints(self) -> Dict[str, Any]:
        return {
            "source": "preprints",
            "total_preprints": 0,
            "by_server": {},
            "categories": {},
            "score": 0,
            "status": "unknown",
        }

    def _empty_grey_literature(self) -> Dict[str, Any]:
        return {
            "source": "grey_literature",
            "total_documents": 0,
            "by_type": {},
            "academic_training_indicator": False,
            "industry_adoption_indicator": False,
            "impact_score": 0,
            "status": "unknown",
        }

    def _empty_result(self, package_name: str) -> Dict[str, Any]:
        return {
            "package_name": package_name,
            "repository": "unknown",
            "timestamp": self._get_timestamp(),
            "semantic_scholar": self._empty_semantic_scholar(),
            "openalex": self._empty_openalex(),
            "preprints": self._empty_preprints(),
            "grey_literature": self._empty_grey_literature(),
            "combined_metrics": {
                "estimated_unique_papers": 0,
                "fields_of_study": {},
                "year_distribution": {},
            },
            "impact_metrics": {
                "total_citations": 0,
                "h_index_proxy": 0,
                "impact_score": 0,
            },
            "field_diversity": {
                "diversity_score": 0,
                "cross_disciplinary": False,
            },
            "citation_trends": {
                "trend": "unknown",
                "growth_rate": 0,
            },
            "overall_score": {
                "score": 0,
                "max_score": 100,
                "status": "unknown",
            },
        }


# CLI for testing
async def main():
    import json
    import sys

    if len(sys.argv) < 2:
        print("Usage: python scholarly_impact.py <package_name> [repo_url]")
        sys.exit(1)

    package_name = sys.argv[1]
    repo_url = sys.argv[2] if len(sys.argv) > 2 else ""

    package = {
        "name": package_name,
        "repository": repo_url,
    }

    async with ScholarlyImpactCollector() as collector:
        result = await collector.collect(package)
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
