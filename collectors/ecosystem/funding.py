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

from collectors.ecosystem.base import COLLECTION_GAP, GitHubCollectorBase, RepoTree, RetryingTransport, get_threshold

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

        async with httpx.AsyncClient(timeout=30.0, transport=RetryingTransport()) as client:
            tree = await RepoTree.fetch(client, self.github_headers, owner, repo)
            results = await asyncio.gather(
                self._find_funding_files(client, owner, repo, tree),
                self._find_grant_references(client, owner, repo),
                self._get_affiliations(client, owner, repo),
                self._get_owner_type(client, owner),
                return_exceptions=True,
            )

        if isinstance(results[0], Exception):
            logger.warning(f"COLLECTION-GAP category=funding_files reason=exception:{results[0]!r}")
            funding_files = {"found": [], "platforms": [], "not_collected": True}
        else:
            funding_files = results[0]

        if isinstance(results[1], Exception):
            logger.warning(f"COLLECTION-GAP category=grants reason=exception:{results[1]!r}")
            grants, grants_gap = [], True
        else:
            grants, grants_gap = results[1]

        if isinstance(results[2], Exception):
            logger.warning(f"COLLECTION-GAP category=affiliations reason=exception:{results[2]!r}")
            affiliations = {"organizations": [], "sampled": 0, "with_affiliation": 0, "gap": True}
        else:
            affiliations = results[2]

        if isinstance(results[3], Exception):
            logger.warning(f"COLLECTION-GAP category=owner_type reason=exception:{results[3]!r}")
            owner_type, owner_type_gap = None, True
        else:
            owner_type, owner_type_gap = results[3]

        return {
            "package_name": repo_name,
            "repository": f"{owner}/{repo}",
            "timestamp": self._get_timestamp(),
            "funding_files": funding_files,
            "grants": grants,
            "affiliations": affiliations,
            "owner_type": owner_type,
            "overall_score": self._calculate_score(
                funding_files, grants, affiliations, owner_type, grants_gap, owner_type_gap
            ),
        }

    # ------------------------------------------------------------------ fetch

    async def _find_funding_files(
        self, client: httpx.AsyncClient, owner: str, repo: str, tree
    ) -> Dict[str, Any]:
        """Locate funding manifests and read the platforms they declare.

        Presence is resolved against a RepoTree (case-insensitive), rather
        than probed one literal path at a time -- see METRIC_BLIND_SPOTS.md
        class F1.
        """
        found, platforms = [], []
        saw_gap = tree is COLLECTION_GAP
        for path in ([] if saw_gap else _FUNDING_FILES):
            real_path = tree.match([path])
            if not real_path:
                continue
            found.append({"path": real_path, "url": tree.match_url([path])})
            plats, plat_gap = await self._read_funding_platforms(client, owner, repo, real_path)
            platforms.extend(plats)
            saw_gap = saw_gap or plat_gap
        # Preserve first-seen order while removing duplicates across files.
        result = {"found": found, "platforms": list(dict.fromkeys(platforms))}
        # A gap that never turned up a file isn't a confirmed "no funding
        # docs" -- a found file (and whatever platforms it named) is real
        # regardless of gaps elsewhere.
        if not found and saw_gap:
            result["not_collected"] = True
        return result

    async def _read_funding_platforms(
        self, client: httpx.AsyncClient, owner: str, repo: str, path: str
    ) -> tuple:
        """Parse a FUNDING.yml into the list of platforms it names.

        Returns (platforms, saw_gap).
        """
        data = await self._github_get(
            client, f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
        )
        if data is COLLECTION_GAP:
            return [], True
        if data is None:
            return [], False
        try:
            content = base64.b64decode(data.get("content", "")).decode("utf-8", "replace")
            parsed = yaml.safe_load(content)
        except Exception as e:
            logger.debug(f"Could not parse {path}: {e}")
            return [], False
        if not isinstance(parsed, dict):
            return [], False
        # Keys with a falsy value are commented-out placeholders, not real sponsors.
        return [k for k, v in parsed.items() if v], False

    async def _find_grant_references(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> tuple:
        """Award and contract numbers acknowledged in the README.

        Returns (grants, saw_gap).
        """
        data = await self._github_get(client, f"https://api.github.com/repos/{owner}/{repo}/readme")
        if data is COLLECTION_GAP:
            return [], True
        if data is None:
            return [], False
        try:
            text = base64.b64decode(data.get("content", "")).decode("utf-8", "replace")
        except Exception as e:
            logger.debug(f"Could not decode README: {e}")
            return [], False

        seen, grants = set(), []
        for pattern, kind in _GRANT_PATTERNS:
            for match in re.findall(pattern, text, flags=re.IGNORECASE):
                value = match.strip()
                if value.lower() not in seen:
                    seen.add(value.lower())
                    grants.append({"value": value, "kind": kind})
        return grants, False

    async def _get_affiliations(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> Dict[str, Any]:
        """Organizations declared by the project's most active contributors."""
        contributors = await self._github_get(
            client, f"https://api.github.com/repos/{owner}/{repo}/contributors",
            params={"per_page": _AFFILIATION_SAMPLE},
        )
        if contributors is COLLECTION_GAP:
            return {"organizations": [], "sampled": 0, "with_affiliation": 0, "gap": True}
        logins = [c["login"] for c in (contributors or []) if c.get("login")]

        async def company_of(login: str):
            data = await self._github_get(client, f"https://api.github.com/users/{login}")
            if data is COLLECTION_GAP:
                return COLLECTION_GAP
            return (data or {}).get("company")

        results = await asyncio.gather(*[company_of(l) for l in logins])

        # Group by canonical key so "The HDF Group", "HDFGroup" and "The HDFgroup"
        # count as one organization rather than inflating the diversity figure.
        counts: Dict[str, int] = {}
        display: Dict[str, str] = {}
        with_affiliation = 0
        gapped = 0
        for value in results:
            if value is COLLECTION_GAP:
                gapped += 1
                continue
            if not value:
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
            # A contributor whose profile lookup gapped is dropped from the
            # sample -- neither confirmed affiliated nor confirmed not.
            "sampled": len(logins) - gapped,
            "with_affiliation": with_affiliation,
            "gap": gapped > 0,
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

    async def _get_owner_type(self, client: httpx.AsyncClient, owner: str) -> tuple:
        """Whether the repository sits under an Organization or a User account.

        Returns (type_or_None, saw_gap).
        """
        data = await self._github_get(client, f"https://api.github.com/users/{owner}")
        if data is COLLECTION_GAP:
            return None, True
        if data is None:
            return None, False
        return data.get("type"), False

    # ---------------------------------------------------------------- scoring

    def _calculate_score(
        self, funding_files: Dict, grants: List, affiliations: Dict, owner_type: Optional[str],
        grants_gap: bool = False, owner_type_gap: bool = False,
    ) -> Dict[str, Any]:
        sub: Dict[str, Dict[str, Any]] = {}
        files_gap = funding_files.get("not_collected", False)
        affil_gap = affiliations.get("gap", False)

        files = funding_files.get("found", [])
        doc_parts = []
        if files:
            doc_parts.append(", ".join(f["path"] for f in files))
        if grants:
            doc_parts.append(f"{len(grants)} award reference(s)")
        doc_passing = bool(files or grants)
        doc_entry: Dict[str, Any] = {
            "label": "Enhanced Funding Documentation Analysis",
            "value": "; ".join(doc_parts) if doc_parts else "No funding documentation found",
            "detail": ", ".join(g["value"] for g in grants) if grants else None,
            "passing": doc_passing,
        }
        if not doc_passing and (files_gap or grants_gap):
            doc_entry["not_collected"] = True
        sub["funding_documentation"] = doc_entry

        orgs = affiliations.get("organizations", [])
        affil_passing = len(orgs) >= get_threshold("4.2.8", "Institutional Affiliation Tracking")
        affil_entry: Dict[str, Any] = {
            "label": "Institutional Affiliation Tracking",
            "value": f"{len(orgs)} organizations across "
                     f"{affiliations.get('with_affiliation', 0)}/"
                     f"{affiliations.get('sampled', 0)} top contributors",
            "detail": ", ".join(o["name"] for o in orgs[:5]) if orgs else None,
            "passing": affil_passing,
        }
        if not affil_passing and affil_gap:
            affil_entry["not_collected"] = True
        sub["institutional_affiliation"] = affil_entry

        platforms = funding_files.get("platforms", [])
        org_owned = owner_type == "Organization"
        corporate_signals = list(platforms)
        if org_owned:
            corporate_signals.append("organization-owned repository")
        corp_passing = bool(corporate_signals)
        corp_entry: Dict[str, Any] = {
            "label": "Corporate Sponsorship Detection",
            "value": ", ".join(corporate_signals) if corporate_signals
                     else "No sponsorship signals found",
            "passing": corp_passing,
        }
        if not corp_passing and (files_gap or owner_type_gap):
            corp_entry["not_collected"] = True
        sub["corporate_sponsorship"] = corp_entry

        # Distinct sources: each declared platform, plus each award reference.
        source_count = len(platforms) + len(grants)
        portfolio_passing = source_count >= get_threshold("4.2.8", "Funding Portfolio Analysis")
        portfolio_entry: Dict[str, Any] = {
            "label": "Funding Portfolio Analysis",
            "value": f"{source_count} distinct funding source(s)",
            "passing": portfolio_passing,
        }
        if not portfolio_passing and (files_gap or grants_gap):
            portfolio_entry["not_collected"] = True
        sub["funding_portfolio"] = portfolio_entry

        sub["nih_r50"] = {
            "label": "NIH R50 Award Tracking",
            "value": None, "passing": False, "not_collected": True,
        }

        # 4.2.9 shares the affiliation pass.
        support_entry: Dict[str, Any] = {
            "label": "Institutional Support Tracking",
            "value": f"{len(orgs)} distinct organizations backing contributors",
            "detail": ", ".join(f"{o['name']} ({o['contributors']})" for o in orgs[:5])
                      if orgs else None,
            "passing": affil_passing,
        }
        if not affil_passing and affil_gap:
            support_entry["not_collected"] = True
        sub["institutional_support"] = support_entry

        # Score only the 4.2.8 rows here; the orchestrator scores 4.2.9 separately.
        financial_keys = [
            "funding_documentation", "institutional_affiliation",
            "corporate_sponsorship", "funding_portfolio", "nih_r50",
        ]
        scorable = [k for k in financial_keys if not sub[k].get("not_collected")]
        score = sum(1 for k in scorable if sub[k].get("passing"))
        max_score = len(scorable)
        if not max_score:
            return {
                "score": None, "max_score": 0, "percentage": None,
                "status": "not_collected", "sub_scores": sub,
            }
        return {
            "score": score,
            "max_score": max_score,
            "percentage": round(score / max_score * 100, 2),
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
