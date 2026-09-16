"""
FAIR and License Exception Collector (CASS Report Section 4.2.2)

Fills the three sub-metrics LicensingCollector does not cover:

  - Automated FAIR4RS Assessment : the four FAIR principles, scored individually
  - License Exception Handling   : licenses GitHub's classifier cannot name
  - FAIR Metadata Assessment     : completeness of the citation metadata

Enhanced License Detection and OSI License Validation stay with licensing.py.

The exception check exists because GitHub's License API returns NOASSERTION for
any license it does not recognise verbatim — HDF5's LICENSE says plainly that
the software "is covered by the 3-clause BSD License", but carries additional
copyright notices, so the API reports "Other" and a naive collector records the
project as unlicensed.
"""

import asyncio
import base64
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx
import yaml

from collectors.ecosystem.base import COLLECTION_GAP, GitHubCollectorBase, RetryingTransport

logger = logging.getLogger(__name__)

# GitHub reports these when it cannot match the text to a known license.
_UNCLASSIFIED = {None, "", "NOASSERTION", "Other", "unknown"}

# License families recoverable from prose, ordered most specific first so
# "3-clause BSD" is not swallowed by a bare "BSD" match.
_LICENSE_TEXT_PATTERNS = [
    ("BSD-3-Clause", r"\b(?:3[- ]clause BSD|BSD 3[- ]clause|new BSD|modified BSD)\b"),
    ("BSD-2-Clause", r"\b(?:2[- ]clause BSD|BSD 2[- ]clause|simplified BSD)\b"),
    ("Apache-2.0", r"\bApache Licen[sc]e,? Version 2\.0\b"),
    ("MIT", r"\bMIT Licen[sc]e\b"),
    ("LGPL", r"\bGNU Lesser General Public Licen[sc]e\b"),
    ("GPL", r"\bGNU General Public Licen[sc]e\b"),
    ("MPL-2.0", r"\bMozilla Public Licen[sc]e,? (?:Version )?2\.0\b"),
    ("BSD", r"\bBSD Licen[sc]e\b"),
]

# Markers that the licence carries terms beyond the standard grant.
_EXCEPTION_MARKERS = [
    ("Named exception", r"\bwith\s+(?:the\s+)?[A-Za-z0-9 ]{2,30}\s+exceptions?\b"),
    ("Additional terms", r"\badditional (?:terms|conditions|restrictions)\b"),
    ("Dual licensing", r"\b(?:dual[- ]licen[sc]ed|either .{0,20}licen[sc]e)\b"),
]

# Counted rather than matched: copyright lines are usually separated by blank
# lines, so a regex requiring consecutive lines never fires on a real licence.
_COPYRIGHT_LINE = re.compile(r"(?im)^\s*copyright\b.*$")
_MULTIPLE_HOLDERS = 3

_CITATION_PATHS = ["CITATION.cff"]
_CODEMETA_PATHS = ["codemeta.json"]
_ZENODO_PATHS = [".zenodo.json", "zenodo.json"]

# Fields a citation record needs before it is genuinely reusable metadata.
_CITATION_FIELDS = ["title", "authors", "version", "license", "repository-code", "doi"]
_MIN_CITATION_FIELDS = 4

# FAIR principles satisfied before the assessment passes.
_MIN_FAIR_PRINCIPLES = 3


class FairLicensingCollector(GitHubCollectorBase):
    """Collects FAIR compliance and license-exception signals (Section 4.2.2)."""

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        repo_name = package.get("name", "Unknown")
        owner_repo = self._extract_owner_repo(package.get("repo_url", ""))
        if not owner_repo:
            logger.error(f"Could not extract owner/repo from {package.get('repo_url')}")
            return self._empty_result(repo_name)

        owner, repo = owner_repo
        logger.info(f"Collecting FAIR and licensing detail for {repo_name}")

        async with httpx.AsyncClient(timeout=30.0, transport=RetryingTransport()) as client:
            results = await asyncio.gather(
                self._get_license(client, owner, repo),
                self._get_citation(client, owner, repo),
                self._any_exists(client, owner, repo, _CODEMETA_PATHS),
                self._any_exists(client, owner, repo, _ZENODO_PATHS),
                self._has_releases(client, owner, repo),
                return_exceptions=True,
            )

        names = ["license", "citation", "codemeta", "zenodo", "releases"]
        defaults = [
            ({"spdx_id": None, "text": ""}, True),
            ({}, True),
            (False, True),
            (False, True),
            (False, True),
        ]
        unpacked = []
        for name, default, value in zip(names, defaults, results):
            if isinstance(value, Exception):
                logger.warning(f"COLLECTION-GAP category={name} reason=exception:{value!r}")
                unpacked.append(default)
            else:
                unpacked.append(value)

        (license_data, license_gap) = unpacked[0]
        (citation, citation_gap) = unpacked[1]
        (has_codemeta, codemeta_gap) = unpacked[2]
        (has_zenodo, zenodo_gap) = unpacked[3]
        (releases, releases_gap) = unpacked[4]

        exceptions = self._analyze_license_text(license_data, license_gap)
        metadata = self._analyze_citation(citation, citation_gap)
        fair = self._assess_fair(
            exceptions, metadata, has_codemeta, has_zenodo, releases,
            codemeta_gap, zenodo_gap, releases_gap,
        )

        return {
            "package_name": repo_name,
            "repository": f"{owner}/{repo}",
            "timestamp": self._get_timestamp(),
            "license_exceptions": exceptions,
            "citation_metadata": metadata,
            "fair": fair,
            "overall_score": self._calculate_score(exceptions, metadata, fair),
        }

    # ------------------------------------------------------------------ fetch

    async def _get_license(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> tuple:
        """SPDX id from the API plus the raw licence text. Returns (data, saw_gap)."""
        data = await self._github_get(client, f"https://api.github.com/repos/{owner}/{repo}/license")
        if data is COLLECTION_GAP:
            return {"spdx_id": None, "text": ""}, True
        if data is None:
            return {"spdx_id": None, "text": ""}, False
        text = ""
        if data.get("content"):
            text = base64.b64decode(data["content"]).decode("utf-8", "replace")
        return {
            "spdx_id": (data.get("license") or {}).get("spdx_id"),
            "name": (data.get("license") or {}).get("name"),
            "text": text,
        }, False

    async def _get_citation(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> tuple:
        """Parsed CITATION.cff, or an empty dict if absent or unparseable.

        Returns (citation, saw_gap).
        """
        saw_gap = False
        for path in _CITATION_PATHS:
            data = await self._github_get(
                client, f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
            )
            if data is COLLECTION_GAP:
                saw_gap = True
                continue
            if data is None:
                continue
            try:
                text = base64.b64decode(data.get("content", "")).decode("utf-8", "replace")
                parsed = yaml.safe_load(text)
                if isinstance(parsed, dict):
                    return parsed, saw_gap
            except Exception as e:
                logger.debug(f"Could not parse {path}: {e}")
        return {}, saw_gap

    async def _any_exists(
        self, client: httpx.AsyncClient, owner: str, repo: str, paths: List[str]
    ) -> tuple:
        """Returns (found, saw_gap)."""
        saw_gap = False
        for path in paths:
            result = await self._check_file_exists(client, owner, repo, path)
            if result is COLLECTION_GAP:
                saw_gap = True
                continue
            if result:
                return True, saw_gap
        return False, saw_gap

    async def _has_releases(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> tuple:
        """Returns (has_releases, saw_gap)."""
        data = await self._github_get(
            client, f"https://api.github.com/repos/{owner}/{repo}/releases",
            params={"per_page": 1},
        )
        if data is COLLECTION_GAP:
            return False, True
        return bool(data), False

    # ---------------------------------------------------------------- analyze

    def _analyze_license_text(
        self, license_data: Dict[str, Any], saw_gap: bool = False
    ) -> Dict[str, Any]:
        """Recover a license family the API could not name, and flag extra terms."""
        spdx = license_data.get("spdx_id")
        text = license_data.get("text") or ""
        classified = spdx not in _UNCLASSIFIED

        resolved = None
        if not classified and text:
            for name, pattern in _LICENSE_TEXT_PATTERNS:
                if re.search(pattern, text, re.IGNORECASE):
                    resolved = name
                    break

        markers = [
            label for label, pattern in _EXCEPTION_MARKERS
            if text and re.search(pattern, text, re.IGNORECASE)
        ]
        if text and len(_COPYRIGHT_LINE.findall(text)) >= _MULTIPLE_HOLDERS:
            markers.append("Multiple copyright holders")
        identified = classified or bool(resolved)
        result = {
            "api_spdx": spdx,
            "api_classified": classified,
            "resolved_from_text": resolved,
            "exception_markers": markers,
            "identified": identified,
        }
        # An unidentified result built on a gap isn't a confirmed "no
        # license" -- an identified one stands regardless, since it came
        # from text/SPDX data that did come back.
        if not identified and saw_gap:
            result["not_collected"] = True
        return result

    def _analyze_citation(
        self, citation: Dict[str, Any], saw_gap: bool = False
    ) -> Dict[str, Any]:
        """Which of the expected citation fields are actually populated."""
        if not citation:
            result = {"exists": False, "present": [], "missing": _CITATION_FIELDS}
            if saw_gap:
                result["not_collected"] = True
            return result

        present = []
        for field in _CITATION_FIELDS:
            if field == "doi":
                # A DOI may sit at the top level or inside identifiers.
                ids = citation.get("identifiers") or []
                has_doi = bool(citation.get("doi")) or any(
                    isinstance(i, dict) and i.get("type") == "doi" for i in ids
                )
                if has_doi:
                    present.append(field)
            elif citation.get(field):
                present.append(field)
        return {
            "exists": True,
            "present": present,
            "missing": [f for f in _CITATION_FIELDS if f not in present],
        }

    @staticmethod
    def _or_gap(*operands: tuple) -> tuple:
        """OR over (value, is_uncertain) pairs: any confirmed True wins outright;
        otherwise any uncertain operand makes the result uncertain; otherwise
        every operand was a confirmed False, so the result is too.
        """
        if any(v for v, _ in operands):
            return True, False
        if any(u for _, u in operands):
            return False, True
        return False, False

    @staticmethod
    def _and_gap(*operands: tuple) -> tuple:
        """AND over (value, is_uncertain) pairs: any confirmed False wins
        outright (ANDing with a real False can't become True); otherwise any
        uncertain operand makes the result uncertain; otherwise every operand
        was a confirmed True.
        """
        if any((not v) and (not u) for v, u in operands):
            return False, False
        if any(u for _, u in operands):
            return False, True
        return True, False

    def _assess_fair(
        self, exceptions: Dict, metadata: Dict,
        has_codemeta: bool, has_zenodo: bool, releases: bool,
        codemeta_gap: bool = False, zenodo_gap: bool = False, releases_gap: bool = False,
    ) -> Dict[str, Any]:
        """Score the four FAIR4RS principles independently.

        Each principle is an AND/OR of signals that may themselves be
        gap-tainted. _or_gap/_and_gap propagate that uncertainty correctly:
        a principle stays a confirmed True/False whenever the confirmed data
        alone already determines it, and is only reported uncertain when a
        gap could actually have changed the outcome.
        """
        doi_present = "doi" in metadata.get("present", [])
        doi_uncertain = not doi_present and bool(metadata.get("not_collected"))
        zenodo_uncertain = not has_zenodo and zenodo_gap
        findable, findable_gap = self._or_gap(
            (doi_present, doi_uncertain), (has_zenodo, zenodo_uncertain)
        )

        identified = exceptions.get("identified", False)
        identified_uncertain = not identified and bool(exceptions.get("not_collected"))
        accessible, accessible_gap = identified, identified_uncertain

        metadata_exists = metadata.get("exists", False)
        metadata_uncertain = not metadata_exists and bool(metadata.get("not_collected"))
        codemeta_uncertain = not has_codemeta and codemeta_gap
        interoperable, interoperable_gap = self._or_gap(
            (metadata_exists, metadata_uncertain), (has_codemeta, codemeta_uncertain)
        )

        releases_uncertain = not releases and releases_gap
        reusable, reusable_gap = self._and_gap(
            (identified, identified_uncertain), (releases, releases_uncertain)
        )

        principles = {
            "Findable": findable,
            "Accessible": accessible,
            "Interoperable": interoperable,
            "Reusable": reusable,
        }
        principle_gaps = {
            "Findable": findable_gap,
            "Accessible": accessible_gap,
            "Interoperable": interoperable_gap,
            "Reusable": reusable_gap,
        }
        return {
            "principles": principles,
            "principle_gaps": principle_gaps,
            "satisfied": [k for k, v in principles.items() if v],
            "count": sum(principles.values()),
        }

    # ---------------------------------------------------------------- scoring

    def _calculate_score(
        self, exceptions: Dict, metadata: Dict, fair: Dict
    ) -> Dict[str, Any]:
        sub: Dict[str, Dict[str, Any]] = {}

        satisfied = fair.get("satisfied", [])
        fair_passing = fair.get("count", 0) >= _MIN_FAIR_PRINCIPLES
        fair_entry: Dict[str, Any] = {
            "label": "Automated FAIR4RS Assessment",
            "value": f"{fair.get('count', 0)}/4 principles satisfied",
            "detail": ", ".join(satisfied) if satisfied else None,
            "passing": fair_passing,
        }
        # A below-threshold count is unconfirmed if any of the principles
        # currently counted against it could have flipped from a gap.
        if not fair_passing and any(fair.get("principle_gaps", {}).values()):
            fair_entry["not_collected"] = True
        sub["fair4rs_assessment"] = fair_entry

        if exceptions.get("api_classified"):
            value = f"{exceptions['api_spdx']} recognised by the GitHub classifier"
        elif exceptions.get("resolved_from_text"):
            value = (f"Reported as \"{exceptions.get('api_spdx') or 'none'}\"; "
                     f"text identifies {exceptions['resolved_from_text']}")
        else:
            value = "License could not be identified from the API or the text"
        exc_passing = exceptions.get("identified", False)
        exc_entry: Dict[str, Any] = {
            "label": "License Exception Handling",
            "value": value,
            "detail": ", ".join(exceptions.get("exception_markers", [])) or None,
            "passing": exc_passing,
        }
        if not exc_passing and exceptions.get("not_collected"):
            exc_entry["not_collected"] = True
        sub["license_exception_handling"] = exc_entry

        present = metadata.get("present", [])
        meta_passing = len(present) >= _MIN_CITATION_FIELDS
        meta_entry: Dict[str, Any] = {
            "label": "FAIR Metadata Assessment",
            "value": f"{len(present)}/{len(_CITATION_FIELDS)} citation fields present"
                     if metadata.get("exists") else "No CITATION.cff found",
            "detail": ", ".join(present) if present else None,
            "passing": meta_passing,
        }
        if not meta_passing and metadata.get("not_collected"):
            meta_entry["not_collected"] = True
        sub["fair_metadata"] = meta_entry

        scorable = {k: v for k, v in sub.items() if not v.get("not_collected")}
        score = sum(1 for s in scorable.values() if s["passing"])
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
        exceptions = {"api_spdx": None, "api_classified": False,
                      "resolved_from_text": None, "exception_markers": [],
                      "identified": False}
        metadata = {"exists": False, "present": [], "missing": _CITATION_FIELDS}
        fair = {"principles": {}, "satisfied": [], "count": 0}
        return {
            "package_name": repo_name,
            "repository": "unknown",
            "timestamp": self._get_timestamp(),
            "license_exceptions": exceptions,
            "citation_metadata": metadata,
            "fair": fair,
            "overall_score": self._calculate_score(exceptions, metadata, fair),
        }
