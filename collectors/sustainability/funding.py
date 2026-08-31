"""
Funding and Institutional Support Collector
(CASS Report Sections 4.2.8 Financial Sustainability and
 4.2.9 Institutional & Organizational Support)

Both sections rest on the same two questions — who pays for this work, and
which organizations do the contributors belong to — so they share one collector
and one pass over the contributor list rather than fetching it twice.

Collected:
  4.2.8  Enhanced Funding Documentation Analysis  : FUNDING.yml, funding.json,
                                                    grant/award numbers in the README
         Institutional Affiliation Tracking       : contributor `company` fields
         Corporate Sponsorship Detection          : funding platforms, org ownership
         Funding Portfolio Analysis               : count of distinct sources
  4.2.9  Institutional Support Tracking           : distinct organizations backing
                                                    the top contributors

Not collected: NIH R50 award tracking (needs the NIH RePORTER API), RSE position
detection, career development indicators and institutional policy analysis — the
report itself notes these need LinkedIn or institutional directory data.
"""

import asyncio
import base64
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx
import yaml

from collectors.sustainability.base import GitHubCollectorBase

logger = logging.getLogger(__name__)

_FUNDING_FILES = [
    ".github/FUNDING.yml", ".github/FUNDING.yaml", "FUNDING.yml", "funding.json",
]

# Award-number shapes used by the agencies that fund this portfolio.
# Deliberately narrow: a looser pattern matches version strings and issue numbers.
_GRANT_PATTERNS = [
    (r"\bDE-[A-Z]{2}\d{2}-?\d{2}[A-Z]{2}\d{5}\b", "DOE contract"),
    (r"\bDE-(?:AC|SC|EE|NA)\d{2}-?\d*[A-Z]*\d*\b", "DOE award"),
    (r"\b(?:NSF|OAC|ACI|SI2|CSSI)[- ]\d{6,7}\b", "NSF award"),
    (r"\b(?:R01|R50|U24|P41)[A-Z]{2}\d{6}\b", "NIH award"),
    (r"\bgrant (?:no\.?|number)?\s*#?\s*\d{6,}\b", "grant number"),
]

# Contributors sampled for affiliation. The GitHub Users API is one call each,
# so this is capped; top contributors carry most of the signal anyway.
_AFFILIATION_SAMPLE = 25

# Company strings that say nothing about institutional backing.
_NOISE_AFFILIATIONS = {"", "-", "none", "n/a", "freelance", "independent", "self", "self-employed"}

_MIN_FUNDING_SOURCES = 2
_MIN_DISTINCT_ORGS = 3


class FundingCollector(GitHubCollectorBase):
    """Collects funding and institutional-affiliation signals (4.2.8 and 4.2.9)."""

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        repo_name = package.get("name", "Unknown")
        owner_repo = self._extract_owner_repo(package.get("repo_url", ""))
        if not owner_repo:
            logger.error(f"Could not extract owner/repo from {package.get('repo_url')}")
            return self._empty_result(repo_name)

        owner, repo = owner_repo
        logger.info(f"Collecting funding and institutional metrics for {repo_name}")

        async with httpx.AsyncClient(timeout=30.0) as client:
            funding_files, grants, affiliations, owner_type = await asyncio.gather(
                self._find_funding_files(client, owner, repo),
                self._find_grant_references(client, owner, repo),
                self._get_affiliations(client, owner, repo),
                self._get_owner_type(client, owner),
                return_exceptions=True,
            )

        if isinstance(funding_files, Exception):
            logger.warning(f"Funding file scan failed: {funding_files}")
            funding_files = {"found": [], "platforms": []}
        if isinstance(grants, Exception):
            logger.warning(f"Grant scan failed: {grants}")
            grants = []
        if isinstance(affiliations, Exception):
            logger.warning(f"Affiliation lookup failed: {affiliations}")
            affiliations = {"organizations": [], "sampled": 0, "with_affiliation": 0}
        if isinstance(owner_type, Exception):
            logger.warning(f"Owner type lookup failed: {owner_type}")
            owner_type = None

        return {
            "package_name": repo_name,
            "repository": f"{owner}/{repo}",
            "timestamp": self._get_timestamp(),
            "funding_files": funding_files,
            "grants": grants,
            "affiliations": affiliations,
            "owner_type": owner_type,
            "overall_score": self._calculate_score(
                funding_files, grants, affiliations, owner_type
            ),
        }

    # ------------------------------------------------------------------ fetch

    async def _find_funding_files(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> Dict[str, Any]:
        """Locate funding manifests and read the platforms they declare."""
        found, platforms = [], []
        for path in _FUNDING_FILES:
            url = await self._check_file_exists(client, owner, repo, path)
            if not url:
                continue
            found.append({"path": path, "url": url})
            platforms.extend(await self._read_funding_platforms(client, owner, repo, path))
        # Preserve first-seen order while removing duplicates across files.
        return {"found": found, "platforms": list(dict.fromkeys(platforms))}

    async def _read_funding_platforms(
        self, client: httpx.AsyncClient, owner: str, repo: str, path: str
    ) -> List[str]:
        """Parse a FUNDING.yml into the list of platforms it names."""
        try:
            resp = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/contents/{path}",
                headers=self.github_headers,
            )
            if resp.status_code != 200:
                return []
            content = base64.b64decode(resp.json().get("content", "")).decode("utf-8", "replace")
            data = yaml.safe_load(content)
            if not isinstance(data, dict):
                return []
            # Keys with a falsy value are commented-out placeholders, not real sponsors.
            return [k for k, v in data.items() if v]
        except Exception as e:
            logger.debug(f"Could not parse {path}: {e}")
            return []

    async def _find_grant_references(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> List[Dict[str, str]]:
        """Award and contract numbers acknowledged in the README."""
        try:
            resp = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/readme",
                headers=self.github_headers,
            )
            if resp.status_code != 200:
                return []
            text = base64.b64decode(resp.json().get("content", "")).decode("utf-8", "replace")
        except Exception as e:
            logger.debug(f"Could not read README: {e}")
            return []

        seen, grants = set(), []
        for pattern, kind in _GRANT_PATTERNS:
            for match in re.findall(pattern, text, flags=re.IGNORECASE):
                value = match.strip()
                if value.lower() not in seen:
                    seen.add(value.lower())
                    grants.append({"value": value, "kind": kind})
        return grants

    async def _get_affiliations(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> Dict[str, Any]:
        """Organizations declared by the project's most active contributors."""
        resp = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}/contributors?per_page={_AFFILIATION_SAMPLE}",
            headers=self.github_headers,
        )
        if resp.status_code != 200:
            return {"organizations": [], "sampled": 0, "with_affiliation": 0}
        logins = [c["login"] for c in resp.json() if c.get("login")]

        async def company_of(login: str) -> Optional[str]:
            r = await client.get(
                f"https://api.github.com/users/{login}", headers=self.github_headers
            )
            if r.status_code != 200:
                return None
            return r.json().get("company")

        results = await asyncio.gather(
            *[company_of(l) for l in logins], return_exceptions=True
        )

        # Group by canonical key so "The HDF Group", "HDFGroup" and "The HDFgroup"
        # count as one organization rather than inflating the diversity figure.
        counts: Dict[str, int] = {}
        display: Dict[str, str] = {}
        with_affiliation = 0
        for value in results:
            if isinstance(value, Exception) or not value:
                continue
            for org in self._normalize_companies(value):
                key = self._canonical_org(org)
                counts[key] = counts.get(key, 0) + 1
                # Prefer the most readable spelling seen: longest wins, since
                # "The HDF Group" is more informative than "HDFGroup".
                if key not in display or len(org) > len(display[key]):
                    display[key] = org
            with_affiliation += 1

        organizations = sorted(counts.items(), key=lambda kv: (-kv[1], display[kv[0]]))
        return {
            "organizations": [
                {"name": display[k], "contributors": c} for k, c in organizations
            ],
            "sampled": len(logins),
            "with_affiliation": with_affiliation,
        }

    @staticmethod
    def _normalize_companies(raw: str) -> List[str]:
        """Split and tidy a GitHub `company` string into organization names.

        The field is free text: people write "@HDFGroup", "The HDFgroup, CGNS",
        or a sentence. Splitting on commas and stripping the @-handle marker
        gets most of it; anything left is used as-is.
        """
        parts = [p.strip().lstrip("@").strip() for p in raw.split(",")]
        return [p for p in parts if p and p.lower() not in _NOISE_AFFILIATIONS]

    @staticmethod
    def _canonical_org(name: str) -> str:
        """Fold spelling variants of one organization onto a single key.

        Case, spacing, punctuation and a leading article are all noise in the
        free-text `company` field; "The HDF Group" and "HDFGroup" are the same
        employer and must not read as two.
        """
        key = re.sub(r"^the\s+", "", name.strip(), flags=re.IGNORECASE)
        return re.sub(r"[^a-z0-9]", "", key.lower())

    async def _get_owner_type(self, client: httpx.AsyncClient, owner: str) -> Optional[str]:
        """Whether the repository sits under an Organization or a User account."""
        try:
            resp = await client.get(
                f"https://api.github.com/users/{owner}", headers=self.github_headers
            )
            if resp.status_code == 200:
                return resp.json().get("type")
        except Exception as e:
            logger.debug(f"Could not fetch owner type: {e}")
        return None

    # ---------------------------------------------------------------- scoring

    def _calculate_score(
        self, funding_files: Dict, grants: List, affiliations: Dict, owner_type: Optional[str]
    ) -> Dict[str, Any]:
        sub: Dict[str, Dict[str, Any]] = {}

        files = funding_files.get("found", [])
        doc_parts = []
        if files:
            doc_parts.append(", ".join(f["path"] for f in files))
        if grants:
            doc_parts.append(f"{len(grants)} award reference(s)")
        sub["funding_documentation"] = {
            "label": "Enhanced Funding Documentation Analysis",
            "value": "; ".join(doc_parts) if doc_parts else "No funding documentation found",
            "detail": ", ".join(g["value"] for g in grants) if grants else None,
            "passing": bool(files or grants),
        }

        orgs = affiliations.get("organizations", [])
        sub["institutional_affiliation"] = {
            "label": "Institutional Affiliation Tracking",
            "value": f"{len(orgs)} organizations across "
                     f"{affiliations.get('with_affiliation', 0)}/"
                     f"{affiliations.get('sampled', 0)} top contributors",
            "detail": ", ".join(o["name"] for o in orgs[:5]) if orgs else None,
            "passing": len(orgs) >= _MIN_DISTINCT_ORGS,
        }

        platforms = funding_files.get("platforms", [])
        org_owned = owner_type == "Organization"
        corporate_signals = list(platforms)
        if org_owned:
            corporate_signals.append("organization-owned repository")
        sub["corporate_sponsorship"] = {
            "label": "Corporate Sponsorship Detection",
            "value": ", ".join(corporate_signals) if corporate_signals
                     else "No sponsorship signals found",
            "passing": bool(corporate_signals),
        }

        # Distinct sources: each declared platform, plus each award reference.
        source_count = len(platforms) + len(grants)
        sub["funding_portfolio"] = {
            "label": "Funding Portfolio Analysis",
            "value": f"{source_count} distinct funding source(s)",
            "passing": source_count >= _MIN_FUNDING_SOURCES,
        }

        sub["nih_r50"] = {
            "label": "NIH R50 Award Tracking",
            "value": None, "passing": False, "not_collected": True,
        }

        # 4.2.9 shares the affiliation pass.
        sub["institutional_support"] = {
            "label": "Institutional Support Tracking",
            "value": f"{len(orgs)} distinct organizations backing contributors",
            "detail": ", ".join(f"{o['name']} ({o['contributors']})" for o in orgs[:5])
                      if orgs else None,
            "passing": len(orgs) >= _MIN_DISTINCT_ORGS,
        }

        # Score only the 4.2.8 rows here; the orchestrator scores 4.2.9 separately.
        financial_keys = [
            "funding_documentation", "institutional_affiliation",
            "corporate_sponsorship", "funding_portfolio", "nih_r50",
        ]
        score = sum(1 for k in financial_keys if sub[k].get("passing"))
        return {
            "score": score,
            "max_score": len(financial_keys),
            "percentage": round(score / len(financial_keys) * 100, 2),
            "sub_scores": sub,
        }

    def _empty_result(self, repo_name: str) -> Dict[str, Any]:
        return {
            "package_name": repo_name,
            "repository": "unknown",
            "timestamp": self._get_timestamp(),
            "funding_files": {"found": [], "platforms": []},
            "grants": [],
            "affiliations": {"organizations": [], "sampled": 0, "with_affiliation": 0},
            "owner_type": None,
            "overall_score": {"score": 0, "max_score": 5, "percentage": 0, "sub_scores": {}},
        }
