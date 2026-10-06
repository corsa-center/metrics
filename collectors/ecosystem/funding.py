"""
Funding and Institutional Support Collector
(CASS Report Sections 4.2.8 Financial Sustainability and
 4.2.9 Institutional & Organizational Support)

Both sections rest on the same two questions — who pays for this work, and
which organizations do the contributors belong to — so they share one collector
and one pass over the contributor list rather than fetching it twice.

Collected:
  4.2.8  Enhanced Funding Documentation Analysis  : FUNDING.yml, funding.json,
                                                    grant/award numbers and funding
                                                    acknowledgments in the README and
                                                    root NOTICE/ACKNOWLEDGMENTS/FUNDING/COPYRIGHT files
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

from collectors.ecosystem.base import (
    _VENDORED_DIR, COLLECTION_GAP, GitHubCollectorBase, RepoTree, RetryingTransport, get_threshold,
)

logger = logging.getLogger(__name__)

_FUNDING_FILES = [
    ".github/FUNDING.yml", ".github/FUNDING.yaml", "FUNDING.yml", "funding.json",
]

# Award-number shapes, as (pattern, kind). Drawn from how funders' award
# numbers are actually written in project READMEs (US, EU, UK, Germany,
# France, Canada, Japan, Australia, China, Switzerland, Wellcome), and kept
# narrow: a looser pattern matches version strings and issue numbers.
# Patterns with a capture group take the award from context ("... grant
# agreement No 101095998"); the value is the group. Specific formats come
# before the generic contextual one so an award keeps its funder's kind.
# (?-i:...) marks prefixes that are also ordinary words in lower case.
_GRANT_PATTERNS = [
    (r"\bDE-[A-Z]{2}\d{2}-?\d{2}[A-Z]{2}\d{5}\b", "DOE contract"),
    # DE-NA numbers are NNSA's; kept apart so an NNSA acknowledgment beside
    # one (e.g. a lab's operating-contract statement) isn't a second source.
    (r"\bDE-NA-?\d{7}\b", "NNSA contract"),
    # Written both as DE-SC0021354 and DE-SC-0021354.
    (r"\bDE-(?:AC|SC|EE|NA)-?\d{2}-?\d*[A-Z]*\d*\b", "DOE award"),
    (r"\b(?:NSF|OAC|ACI|SI2|CSSI|CCF|CNS|IIS|DMS|DMR|AST|PHY|CHE|EAR|OCE|AGS|ECCS|CBET"
     r"|CMMI|DBI|DEB|IOS|MCB|OPP|SES|BCS|DUE|DRL|OIA)[- ]\d{6,7}\b", "NSF award"),
    # NIH activity code + institute + serial: R01GM123456, U19-AI135995.
    (r"(?-i:\b[RUPKFTS]\d{2}-?[A-Z]{2}\d{6}\b)", "NIH award"),
    (r"(?-i:\b(?:FA|HR|N0|W9|W31)\d{3,4}-\d{2}-[A-Z]-\d{4}\b)", "DoD contract"),
    (r"(?-i:\b(?:EP|ST|NE|MR|BB|ES|AH)/[A-Z]\d{6}/\d\b)", "UKRI award"),
    (r"\bANR-\d{2}-[A-Z0-9]{2,5}-\d{4}(?:-\d{2})?\b", "ANR award"),
    (r"(?-i:\b(?:RGPIN|RGPAS|DGECR|ALLRP|CRDPJ|STPGP)-\d{4}-\d{4,5}\b)", "NSERC award"),
    (r"(?-i:\bJP\d{2}[A-Z]{1,2}\d{4,5}\b)", "JSPS KAKENHI grant"),
    (r"(?:KAKENHI|JSPS)[^.;\n]{0,60}?\b(\d{2}[A-Z]{1,2}\d{4,5})\b", "JSPS KAKENHI grant"),
    (r"(?-i:\bJPMJ[A-Z]{2}\d{2}[A-Z0-9]{2}\b)", "JST grant"),
    (r"(?-i:\b(?:DP|DE|FT|FL|LP|IC|CE|LE|IH|IN)\d{9}\b)", "ARC grant"),
    (r"(?-i:\b(?:EXC|SFB|TRR|GRK)[- ]?\d{3,4}(?:/\d)?\b)", "DFG grant"),
    (r"(?:DFG|Deutsche Forschungsgemeinschaft|German Research Foundation)[^.;\n]{0,100}?"
     r"project[- ]?(?:number|no\.?|id)\s*:?\s*(\d{6,9})\b", "DFG grant"),
    (r"\b\d{6}/Z/\d{2}/Z\b", "Wellcome grant"),
    (r"(?:National Natural Science Foundation of China|NSFC)[^.;\n]{0,80}?"
     r"(?:No\.?|numbers?|grants?)\s*:?\s*(\d{8})\b", "NSFC grant"),
    (r"(?:Swiss National Science Foundation|SNSF|SNF)[^.;\n]{0,80}?"
     r"(?:No\.?|numbers?|grants?)\s*:?\s*(\d{6}(?:_\d{6})?)\b", "SNSF grant"),
    (r"grant agreements?\s*(?:No\.?|n[°o]\.?|nr\.?|number|#)?\s*:?\s*(\d{6,9})\b", "EU grant"),
    # Any other award given by number in context.
    (r"\b(?:grant|award|contract|project)\s+(?:agreement\s+)?(?:No\.?|n[°o]\.?|nr\.?|number|#)"
     r"\s*:?\s*([A-Z0-9][A-Z0-9/_.-]*\d{5,}[A-Z0-9/_.-]*)", "grant number"),
]

# Which funder an award-number kind belongs to, so an acknowledgment naming
# the same funder isn't counted as a second funding source.
_GRANT_AGENCY = {
    "DOE contract": "DOE", "DOE award": "DOE", "NNSA contract": "NNSA",
    "NSF award": "NSF", "NIH award": "NIH",
    "DoD contract": "DoD", "UKRI award": "UKRI", "ANR award": "ANR", "NSERC award": "NSERC",
    "JSPS KAKENHI grant": "JSPS", "JST grant": "JST", "ARC grant": "Australian Research Council",
    "DFG grant": "DFG", "Wellcome grant": "Wellcome", "NSFC grant": "NSFC",
    "SNSF grant": "SNSF", "EU grant": "European Commission",
}

# Root files where projects acknowledge funding besides the README. Federal
# lab codes usually carry it in NOTICE: AMReX's says "developed under funding
# from the U.S. Department of Energy", with no award number anywhere.
_ACKNOWLEDGMENT_FILE = r"^(?:notice|acknowledge?ments?|funding|copyright)(?:\.(?:md|txt|rst))?$"
# Documentation pages that commonly carry the funding statement: the docs
# landing page and any acknowledgments page, in the project's own top-level
# doc tree. Built output (_build/, _sources/) is skipped; the shallowest few
# are read.
_DOC_FUNDING_FILE = (r"^(?:src/)?(?:docs?|documentation|sphinx)/(?:[^/]+/)*"
                     r"(?:index|acknowledge?ments?|funding)\.(?:md|rst|txt)$")
_DOC_BUILD_DIR = re.compile(r"(?:^|/)(?:_build|_sources|build|html)/")
_MAX_DOC_FUNDING_FILES = 3

# Funding agencies an acknowledgment can name, as (agency, pattern).
_AGENCIES = [
    ("DOE", r"(?:US |United States )?Department of Energy|\bDOE\b|Office of Science|Exascale Computing Project"),
    ("NSF", r"National Science Foundation|\bNSF\b"),
    ("NIH", r"National Institutes? of Health|\bNIH\b"),
    ("NASA", r"\bNASA\b|National Aeronautics and Space Administration"),
    ("DoD", r"Department of Defense|\bDoD\b|\bDARPA\b|Office of Naval Research|Army Research|Air Force"),
    ("NNSA", r"National Nuclear Security Administration|\bNNSA\b"),
    ("European Commission", r"European (?:Commission|Research Council|Union)|Horizon (?:2020|Europe)|\bERC\b|\bEuroHPC\b"),
    ("DFG", r"Deutsche Forschungsgemeinschaft|German Research Foundation|\bDFG\b"),
    ("BMBF", r"\bBMBF\b|Federal Ministry of Education and Research"),
    ("UKRI", r"\bUKRI\b|UK Research and Innovation|\b(?:EPSRC|BBSRC|STFC|NERC|ESRC|AHRC)\b"
             r"|Engineering and Physical Sciences Research Council|Innovate UK"),
    ("ANR", r"Agence Nationale de la Recherche|French National Research Agency|\bANR\b"),
    ("SNSF", r"Swiss National Science Foundation|\bSNSF\b"),
    ("NWO", r"\bNWO\b|Dutch Research Council|Netherlands Organisation for Scientific Research"),
    ("NSERC", r"\bNSERC\b|Natural Sciences and Engineering Research Council"),
    ("JSPS", r"\bJSPS\b|KAKENHI|Japan Society for the Promotion of Science"),
    ("JST", r"Japan Science and Technology Agency|\bJST\b"),
    ("Australian Research Council", r"Australian Research Council"),
    ("NSFC", r"National Natural Science Foundation of China|\bNSFC\b"),
    ("Wellcome", r"\bWellcome(?: Trust)?\b"),
]
# An agency counts only inside a funding sentence, not wherever it's named:
# "deployed on DOE HPC systems" or "supports ECP applications" isn't funding.
# `supports` is excluded by the word boundary after `support(ed)`.
_FUNDING_VERB = r"\b(?:fund(?:ed|ing)?|support(?:ed)?|sponsor(?:ed|ship)?|grants?|awards?|financed)\b"
_SENTENCE_WINDOW = 160
_FUNDING_TOPIC = r"(?:acknowledge?ments?|funding|financial support|sponsors?)\b"
# Markdown ATX headings, and underlined (Setext / reStructuredText) ones.
_FUNDING_HEADING = re.compile(
    rf"^\s{{0,3}}#{{1,6}}\s+{_FUNDING_TOPIC}.*$"
    rf"|^[ \t]*{_FUNDING_TOPIC}[^\n]*\n[ \t]*([=\-~^*#])\1{{2,}}[ \t]*$", re.I | re.M)
_NEXT_HEADING = re.compile(r"^\s{0,3}#{1,6}\s|^[^\n]+\n[ \t]*([=\-~^*#])\1{2,}[ \t]*$", re.M)

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
                self._find_grant_references(client, owner, repo, tree),
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
        self, client: httpx.AsyncClient, owner: str, repo: str, tree=None
    ) -> tuple:
        """Award numbers and agency funding acknowledgments in the README,
        root NOTICE / ACKNOWLEDGMENTS / FUNDING files, and the docs landing
        and acknowledgments pages.

        Returns (grants, saw_gap). Each entry is {"value", "kind"}; kind is
        an award-number kind or "acknowledgment" (agency named in a funding
        sentence, no number).
        """
        texts, saw_gap = [], False
        data = await self._github_get(client, f"https://api.github.com/repos/{owner}/{repo}/readme")
        if data is COLLECTION_GAP:
            saw_gap = True
        elif data is not None:
            texts.append(self._decode(data, "README"))

        if tree is COLLECTION_GAP:
            saw_gap = True
        elif tree is not None:
            doc_pages = sorted(
                (p for p in tree.find(_DOC_FUNDING_FILE)
                 if not _VENDORED_DIR.search(p) and not _DOC_BUILD_DIR.search(p)),
                key=lambda p: (p.count("/"), p))[:_MAX_DOC_FUNDING_FILES]
            for path in [p for p in tree.find(_ACKNOWLEDGMENT_FILE) if "/" not in p] + doc_pages:
                data = await self._github_get(
                    client, f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
                )
                if data is COLLECTION_GAP:
                    saw_gap = True
                elif isinstance(data, dict):
                    texts.append(self._decode(data, path))

        seen, grants = set(), []
        for text in texts:
            for pattern, kind in _GRANT_PATTERNS:
                for match in re.findall(pattern, text, flags=re.IGNORECASE):
                    value = match.strip().rstrip(".,;:)")
                    key = re.sub(r"[-\s]", "", value.lower())
                    if key not in seen:
                        seen.add(key)
                        grants.append({"value": value, "kind": kind})
        for agency in self._acknowledged_agencies("\n".join(texts)):
            if agency not in seen:
                seen.add(agency)
                grants.append({"value": agency, "kind": "acknowledgment"})
        return grants, saw_gap

    @staticmethod
    def _decode(data: Dict[str, Any], label: str) -> str:
        try:
            return base64.b64decode(data.get("content", "")).decode("utf-8", "replace")
        except Exception as e:
            logger.debug(f"Could not decode {label}: {e}")
            return ""

    @staticmethod
    def _acknowledged_agencies(text: str) -> List[str]:
        """Agencies named near a funding verb, or anywhere in a section headed
        Acknowledgments / Funding (often a bare list of funders), in
        first-seen order."""
        windows = []
        for m in _FUNDING_HEADING.finditer(text):
            rest = text[m.end():].lstrip("\n")
            nxt = _NEXT_HEADING.search(rest)
            windows.append(rest[:nxt.start()] if nxt else rest[:2000])
        # "U.S." would otherwise read as sentence ends inside the window.
        flat = re.sub(r"\bU\.\s?S\.", "US", re.sub(r"\s+", " ", text))
        for verb in re.finditer(_FUNDING_VERB, flat, re.IGNORECASE):
            windows.append(flat[verb.start(): verb.end() + _SENTENCE_WINDOW].split(". ")[0])
        found: List[str] = []
        for window in windows:
            for agency, pattern in _AGENCIES:
                if agency not in found and re.search(pattern, window, re.IGNORECASE):
                    found.append(agency)
        return found

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
        awards = [g for g in grants if g["kind"] != "acknowledgment"]
        acknowledged = [g for g in grants if g["kind"] == "acknowledgment"]
        if awards:
            doc_parts.append(f"{len(awards)} award reference(s)")
        if acknowledged:
            doc_parts.append("funding acknowledged: " + ", ".join(g["value"] for g in acknowledged))
        doc_passing = bool(files or grants)
        doc_entry: Dict[str, Any] = {
            "label": "Enhanced Funding Documentation Analysis",
            "value": "; ".join(doc_parts) if doc_parts else "No funding documentation found",
            "detail": ", ".join(g["value"] for g in awards) if awards else None,
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

        # Distinct sources: each declared platform, each award reference, and
        # each acknowledged agency none of those awards already belongs to.
        award_agencies = {_GRANT_AGENCY.get(g["kind"]) for g in awards}
        source_count = len(platforms) + len(awards) + sum(
            1 for g in acknowledged if g["value"] not in award_agencies
        )
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
