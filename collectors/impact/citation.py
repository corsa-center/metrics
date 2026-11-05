"""
Citation Metric Collector for CASS Framework

Collects and aggregates citation-related metrics from multiple academic sources.
"""

import asyncio
import httpx
import logging
import re
from typing import Dict, Any, List, Optional
import yaml

from integrations.semantic_scholar import SemanticScholarClient
from integrations.openalex import OpenAlexClient
from integrations.zenodo import ZenodoClient
from forge.base import COLLECTION_GAP, RetryingTransport
from forge.interface import Forge


# Root citation files that carry BibTeX or prose instead of CFF. SUNDIALS
# has no CITATION.cff; its CITATIONS.md lists four papers' DOIs in BibTeX.
_BIBTEX_CITATION_FILES = ["CITATIONS.md", "CITATION.md", "CITATION.bib", "CITATIONS.bib", "CITATION"]
_DOI_IN_TEXT = re.compile(r"10\.\d{4,9}/[^\s{}\"'<>()\[\],;]+")
_MAX_DECLARED_DOIS = 10


def _citation_evidence(declared: Dict[str, Any], trace: Dict[str, Any],
                       name: Optional[str]) -> Dict[str, List[Dict[str, Any]]]:
    """Where the Formal Citations figure came from: each source's count with
    a link to the citing works or matched paper, the DOIs looked up and
    where each was declared, or where a DOI was looked for."""
    found, missing, notes, searched = [], [], [], []
    openalex = trace.get("openalex") or {}
    if openalex.get("count") is not None and openalex.get("work_ids"):
        ids = openalex["work_ids"]
        found.append({
            "text": f"{openalex['count']:,} distinct works citing "
                    + ("this DOI" if len(ids) == 1 else f"these {len(ids)} DOIs"),
            "path": "OpenAlex", "url": "https://openalex.org/works?filter=cites:" + "|".join(ids),
        })
    for doi in openalex.get("unresolved") or []:
        missing.append({"text": f"{doi} in OpenAlex (it has no work for this DOI)"})

    s2 = trace.get("semantic_scholar") or {}
    if s2.get("status") == "found":
        paper = f"{s2.get('title') or 'untitled'}" + (f" ({s2['year']})" if s2.get("year") else "")
        url = (f"https://www.semanticscholar.org/paper/{s2['paper_id']}"
               if s2.get("paper_id") else None)
        if s2.get("via") == "title":
            found.append({"text": f"{s2.get('citation_count', 0):,} citations of the top result "
                                  f"for a title search on “{s2.get('query') or name}”",
                          "path": "Semantic Scholar", "url": url, "quote": paper})
            notes.append({"text": "A title search on the package name can match an unrelated "
                                  "paper; a DOI in CITATION.cff makes the lookup exact."})
        else:
            found.append({"text": f"{s2.get('citation_count', 0):,} citations of the paper",
                          "path": "Semantic Scholar", "url": url, "quote": paper})
    elif s2.get("status") == "not_found":
        missing.append({"text": "a paper for "
                                + ("the first DOI" if declared.get("all") else f"“{name}”")
                                + " in Semantic Scholar"})
    if len(found) > 1:
        notes.append({"text": "Formal Citations is the larger of these counts."})

    dois = declared.get("all") or []
    sources = declared.get("sources") or {}
    if dois:
        searched.append({"text": "DOIs looked up", "paths": [
            {"path": f"{d} (from {sources.get(d, 'the catalog entry')})", "url": f"https://doi.org/{d}"}
            for d in dois]})
    elif declared.get("looked_in"):
        missing.append({"text": "a DOI to look citations up by"})
        searched.append({"text": "DOIs looked for in",
                         "paths": [{"path": p} for p in declared["looked_in"]]})
    return {"found": found, "missing": missing, "notes": notes, "searched": searched}


class CitationMetricCollector:
    """
    Collects citation metrics from academic sources

    Sub-metrics:
    - Formal citations (from papers, DOI resolutions)
    - Informal mentions (blog posts, documentation)
    - Dependent packages/projects
    - Download/usage statistics
    """

    def __init__(self, config: Dict[str, Any], forge: Forge):
        """
        Initialize citation collector

        Args:
            config: Configuration dict with API credentials and weights
            forge: Forge for this package's host (see forge/interface.py)
        """
        self.config = config
        self.forge = forge
        self.logger = logging.getLogger(self.__class__.__name__)

        # Initialize API clients
        credentials = config.get("api_credentials", {})

        self.semantic_scholar = SemanticScholarClient(
            credentials.get("semantic_scholar") or {}
        )
        self.openalex = OpenAlexClient(credentials.get("openalex") or {})
        self.zenodo = ZenodoClient(credentials.get("zenodo") or {})

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

        # The catalog carries no DOI, so without this every lookup fell back
        # to a title search on the bare package name -- AMReX scored 0 formal
        # citations while its JOSS paper has 500+ in OpenAlex.
        declared = await self._declared_dois(package)
        package = {**package, "dois": declared["all"],
                   "doi": package.get("doi") or declared["software"]}
        # What the formal-citation lookups found, for the report's evidence.
        citation_trace: Dict[str, Any] = {}

        # Collect all metrics concurrently
        async with httpx.AsyncClient(timeout=30.0, transport=RetryingTransport()) as client:
            results = await asyncio.gather(
                self._get_formal_citations(package, citation_trace),
                self._get_informal_mentions(package),
                self._get_dependent_packages(client, package),
                self._get_doi_resolutions(package),
                self._get_repo_stats(client, package),
                return_exceptions=True,
            )

        # Unpack results (handle any exceptions)
        # None: every source failed -- not collected, rather than a
        # confident zero.
        formal_citations = results[0] if not isinstance(results[0], Exception) else None
        informal_mentions = results[1] if not isinstance(results[1], Exception) else 0
        dependent_packages = results[2] if not isinstance(results[2], Exception) else 0
        doi_resolutions = results[3] if not isinstance(results[3], Exception) else 0
        repo_stats = results[4] if not isinstance(results[4], Exception) else {"stars": 0, "forks": 0}

        # Log any exceptions
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                metric_names = [
                    "formal_citations",
                    "informal_mentions",
                    "dependent_packages",
                    "doi_resolutions",
                    "repo_stats",
                ]
                self.logger.error(f"Error collecting {metric_names[i]}: {result}")

        # Calculate normalized scores (0-100 scale)
        normalized_scores = {
            "formal_citations": (None if formal_citations is None
                                 else self._normalize_citations(formal_citations)),
            "informal_mentions": self._normalize_mentions(informal_mentions),
            "dependent_packages": self._normalize_dependents(dependent_packages),
            "doi_resolutions": self._normalize_dois(doi_resolutions),
        }

        # Calculate weighted total score
        # A sub-metric that wasn't collected is left out and the remaining
        # weights rescaled, the convention the other dimensions follow.
        collected = {m: w for m, w in self.sub_metric_weights.items()
                     if normalized_scores.get(m) is not None}
        weight_total = sum(collected.values())
        total_score = (sum(normalized_scores[m] * w for m, w in collected.items()) / weight_total
                       * sum(self.sub_metric_weights.values())) if weight_total else 0.0

        return {
            "metric_name": "citation",
            "score": round(total_score, 2),
            "sub_metrics": {
                "formal_citations": {
                    "raw_value": formal_citations,
                    "normalized_score": normalized_scores["formal_citations"],
                    "weight": self.sub_metric_weights["formal_citations"],
                    **({"not_collected": True} if formal_citations is None else {}),
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
            # Unweighted evidence — stars/forks on the project's forge, shown
            # in reports but not part of the weighted score above. `platform`
            # labels them ("GitHub Stars", "GitLab Stars").
            "repo_stats": {
                "stars": repo_stats.get("stars", 0),
                "forks": repo_stats.get("forks", 0),
                "platform": self.forge.display_name,
            },
            # The orchestrator builds 4.1.1's rows itself, so their evidence
            # is keyed by row label here.
            "row_evidence": {"formal_citations": {
                "label": "Formal Citations",
                "evidence": _citation_evidence(declared, citation_trace, package.get("name")),
            }},
            "metadata": {
                "timestamp": asyncio.get_event_loop().time(),
                "package_name": package.get("name"),
                "sources_checked": ["semantic_scholar", "openalex", "zenodo", "github"],
            },
        }

    async def _declared_dois(self, package: Dict[str, Any]) -> Dict[str, Any]:
        """DOIs the project asks to be cited by: from its CITATION.cff, or
        failing that from a BibTeX/Markdown citation file at the root.

        Returns {"software": the record's own DOI or None, "all": every
        distinct DOI -- the software DOI, preferred-citation, references}.
        """
        result: Dict[str, Any] = {"software": None, "all": [], "sources": {}, "looked_in": []}
        catalog = self._normalize_doi(package.get("doi"))
        ref = self.forge.extract_ref(package.get("repo_url") or "")
        if not ref:
            if catalog:
                result.update(all=[catalog], sources={catalog: "the catalog entry"})
            return result
        # Raw reads, not the REST API: no rate-limit quota spent on files
        # most repositories don't have.
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            text = await self.forge.raw_text(client, ref, "CITATION.cff")
            try:
                cff = yaml.safe_load(text) if text else None
            except Exception as e:
                self.logger.debug(f"Could not parse CITATION.cff: {e}")
                cff = None
            # raw_text reads None for a missing file and a failed read alike;
            # the raw host is rarely the one that fails, so "looked_in" says
            # where the reads went, not that each file is confirmed absent.
            looked_in = ["the catalog entry", "CITATION.cff"]
            if isinstance(cff, dict):
                result = {**self._dois_from_cff(cff, package.get("doi")), "looked_in": looked_in}
                result["sources"] = {d: "CITATION.cff" for d in result["all"]}
                if catalog:
                    result["sources"][catalog] = "the catalog entry"
            if result["all"]:
                return result

            for name in _BIBTEX_CITATION_FILES:
                looked_in.append(name)
                dois = self._dois_from_text(await self.forge.raw_text(client, ref, name) or "")
                if dois:
                    # These cite papers, not the software record itself, so
                    # none of them is taken as the software DOI.
                    return {"software": None, "all": dois, "sources": {d: name for d in dois},
                            "looked_in": looked_in}
        if catalog and not result["all"]:
            result.update(all=[catalog], sources={catalog: "the catalog entry"})
        result["looked_in"] = looked_in
        return result

    @classmethod
    def _dois_from_text(cls, text: str) -> List[str]:
        found = [cls._normalize_doi(m) for m in _DOI_IN_TEXT.findall(text)]
        return list(dict.fromkeys(d for d in found if d))[:_MAX_DECLARED_DOIS]

    @staticmethod
    def _normalize_doi(value: Any) -> Optional[str]:
        if not isinstance(value, str):
            return None
        m = re.search(r"10\.\d{4,9}/\S+", value)
        return m.group(0).rstrip(".,;").lower() if m else None

    @classmethod
    def _dois_from_cff(cls, cff: Dict[str, Any], catalog_doi: Optional[str] = None) -> Dict[str, Any]:
        def record_dois(rec: Any) -> List[str]:
            if not isinstance(rec, dict):
                return []
            found = [rec.get("doi")] + [
                i.get("value") for i in rec.get("identifiers") or []
                if isinstance(i, dict) and i.get("type") == "doi"
            ]
            return [d for d in (cls._normalize_doi(f) for f in found) if d]

        own = record_dois(cff)
        cited = own + record_dois(cff.get("preferred-citation"))
        for ref in cff.get("references") or []:
            cited += record_dois(ref)
        catalog = cls._normalize_doi(catalog_doi)
        if catalog:
            cited.insert(0, catalog)
        return {"software": own[0] if own else None, "all": list(dict.fromkeys(cited))}

    async def _get_formal_citations(
        self, package: Dict[str, Any], trace: Optional[Dict[str, Any]] = None
    ) -> Optional[int]:
        """Formal academic citations: the larger of OpenAlex's distinct citing
        works and Semantic Scholar's count, or None if every lookup failed
        (not collected, rather than zero). `trace`, if given, records what
        each source returned, for the report (see _citation_evidence)."""
        trace = {} if trace is None else trace
        doi = package.get("doi")
        dois = package.get("dois") or ([doi] if doi else [])
        name = package.get("name")

        if not dois and not name:
            self.logger.warning("No DOI or name provided for citation lookup")
            return 0

        citations: List[int] = []
        answered = False

        if dois:
            # OpenAlex first (often more comprehensive): distinct works
            # citing any declared DOI, or the best single DOI if the
            # combined query can't be made.
            try:
                works = await self.openalex.citing_works(dois)
                trace["openalex"] = works
                if works["count"] is not None:
                    citations.append(works["count"])
                    answered = True
                else:
                    for d in dois:
                        openalex_data = await self.openalex.get_work_citations(d)
                        if openalex_data.get("status") != "failed":
                            answered = True
                            citations.append(openalex_data.get("cited_by_count", 0))
            except Exception as e:
                self.logger.warning(f"OpenAlex lookup failed: {e}")

        try:
            # Semantic Scholar: by DOI when there is one, else by title --
            # a title search on a bare package name is the weakest signal.
            ss_data = await self.semantic_scholar.get_citations(dois[0] if dois else None, name)
            trace["semantic_scholar"] = ss_data
            if ss_data.get("status") != "failed":
                answered = True
                citations.append(ss_data.get("citation_count", 0))
        except Exception as e:
            self.logger.warning(f"Semantic Scholar lookup failed: {e}")

        if not answered:
            return None
        # The maximum count found (most authoritative source)
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

    async def _get_dependent_packages(
        self, client: httpx.AsyncClient, package: Dict[str, Any]
    ) -> int:
        """Get number of packages/repos that depend on this one (forks as a proxy)"""
        ref = self.forge.extract_ref(package.get("repo_url", ""))
        if not ref:
            return 0

        # Neither GitHub nor GitLab expose a "used by" count via their REST
        # APIs (would need to scrape the web UI or use GraphQL); use forks
        # as a proxy instead. This is independent of whether CITATION.cff
        # exists. A collection gap reads the same as "0 forks" here --
        # unweighted evidence, not scored, so this one field doesn't carry
        # the same not_collected distinction the scored sub-metrics do.
        stats = await self.forge.repo_info(client, ref)
        if not stats or stats is COLLECTION_GAP:
            return 0
        return stats.get("forks", 0)

    async def _get_repo_stats(
        self, client: httpx.AsyncClient, package: Dict[str, Any]
    ) -> Dict[str, int]:
        """Get stars and forks counts (unweighted evidence, not scored)"""
        ref = self.forge.extract_ref(package.get("repo_url", ""))
        if not ref:
            return {"stars": 0, "forks": 0}

        stats = await self.forge.repo_info(client, ref)
        if not stats or stats is COLLECTION_GAP:
            return {"stars": 0, "forks": 0}
        return {"stars": stats.get("stars", 0), "forks": stats.get("forks", 0)}

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
    forge = Forge.for_repo("github", package["repo_url"], config["api_credentials"])
    collector = CitationMetricCollector(config, forge)
    results = await collector.collect(package)

    # Display results
    import json

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
