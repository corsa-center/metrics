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

from collectors.sustainability.base import GitHubCollectorBase

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

        async with httpx.AsyncClient(timeout=30.0) as client:
            license_data, citation, has_codemeta, has_zenodo, releases = await asyncio.gather(
                self._get_license(client, owner, repo),
                self._get_citation(client, owner, repo),
                self._any_exists(client, owner, repo, _CODEMETA_PATHS),
                self._any_exists(client, owner, repo, _ZENODO_PATHS),
                self._has_releases(client, owner, repo),
                return_exceptions=True,
            )

        if isinstance(license_data, Exception):
            logger.warning(f"License fetch failed: {license_data}")
            license_data = {"spdx_id": None, "text": ""}
        if isinstance(citation, Exception):
            logger.warning(f"Citation fetch failed: {citation}")
            citation = {}
        for name, value in [("codemeta", has_codemeta), ("zenodo", has_zenodo),
                            ("releases", releases)]:
            if isinstance(value, Exception):
                logger.warning(f"{name} check failed: {value}")

        has_codemeta = has_codemeta is True
        has_zenodo = has_zenodo is True
        releases = releases is True

        exceptions = self._analyze_license_text(license_data)
        metadata = self._analyze_citation(citation)
        fair = self._assess_fair(
            license_data, exceptions, citation, metadata, has_codemeta, has_zenodo, releases
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
    ) -> Dict[str, Any]:
        """SPDX id from the API plus the raw licence text."""
        resp = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}/license",
            headers=self.github_headers,
        )
        if resp.status_code != 200:
            return {"spdx_id": None, "text": ""}
        data = resp.json()
        text = ""
        if data.get("content"):
            text = base64.b64decode(data["content"]).decode("utf-8", "replace")
        return {
            "spdx_id": (data.get("license") or {}).get("spdx_id"),
            "name": (data.get("license") or {}).get("name"),
            "text": text,
        }

    async def _get_citation(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> Dict[str, Any]:
        """Parsed CITATION.cff, or an empty dict if absent or unparseable."""
        for path in _CITATION_PATHS:
            resp = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/contents/{path}",
                headers=self.github_headers,
            )
            if resp.status_code != 200:
                continue
            try:
                text = base64.b64decode(resp.json().get("content", "")).decode("utf-8", "replace")
                parsed = yaml.safe_load(text)
                return parsed if isinstance(parsed, dict) else {}
            except Exception as e:
                logger.debug(f"Could not parse {path}: {e}")
        return {}

    async def _any_exists(
        self, client: httpx.AsyncClient, owner: str, repo: str, paths: List[str]
    ) -> bool:
        for path in paths:
            if await self._check_file_exists(client, owner, repo, path):
                return True
        return False

    async def _has_releases(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> bool:
        resp = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}/releases?per_page=1",
            headers=self.github_headers,
        )
        return resp.status_code == 200 and bool(resp.json())

    # ---------------------------------------------------------------- analyze

    def _analyze_license_text(self, license_data: Dict[str, Any]) -> Dict[str, Any]:
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
        return {
            "api_spdx": spdx,
            "api_classified": classified,
            "resolved_from_text": resolved,
            "exception_markers": markers,
            "identified": classified or bool(resolved),
        }

    def _analyze_citation(self, citation: Dict[str, Any]) -> Dict[str, Any]:
        """Which of the expected citation fields are actually populated."""
        if not citation:
            return {"exists": False, "present": [], "missing": _CITATION_FIELDS}

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

    def _assess_fair(
        self, license_data: Dict, exceptions: Dict, citation: Dict,
        metadata: Dict, has_codemeta: bool, has_zenodo: bool, releases: bool,
    ) -> Dict[str, Any]:
        """Score the four FAIR4RS principles independently."""
        findable = "doi" in metadata.get("present", []) or has_zenodo
        accessible = exceptions.get("identified", False)
        interoperable = metadata.get("exists", False) or has_codemeta
        reusable = exceptions.get("identified", False) and releases

        principles = {
            "Findable": findable,
            "Accessible": accessible,
            "Interoperable": interoperable,
            "Reusable": reusable,
        }
        return {
            "principles": principles,
            "satisfied": [k for k, v in principles.items() if v],
            "count": sum(principles.values()),
        }

    # ---------------------------------------------------------------- scoring

    def _calculate_score(
        self, exceptions: Dict, metadata: Dict, fair: Dict
    ) -> Dict[str, Any]:
        sub: Dict[str, Dict[str, Any]] = {}

        satisfied = fair.get("satisfied", [])
        sub["fair4rs_assessment"] = {
            "label": "Automated FAIR4RS Assessment",
            "value": f"{fair.get('count', 0)}/4 principles satisfied",
            "detail": ", ".join(satisfied) if satisfied else None,
            "passing": fair.get("count", 0) >= _MIN_FAIR_PRINCIPLES,
        }

        if exceptions.get("api_classified"):
            value = f"{exceptions['api_spdx']} recognised by the GitHub classifier"
        elif exceptions.get("resolved_from_text"):
            value = (f"Reported as \"{exceptions.get('api_spdx') or 'none'}\"; "
                     f"text identifies {exceptions['resolved_from_text']}")
        else:
            value = "License could not be identified from the API or the text"
        sub["license_exception_handling"] = {
            "label": "License Exception Handling",
            "value": value,
            "detail": ", ".join(exceptions.get("exception_markers", [])) or None,
            "passing": exceptions.get("identified", False),
        }

        present = metadata.get("present", [])
        sub["fair_metadata"] = {
            "label": "FAIR Metadata Assessment",
            "value": f"{len(present)}/{len(_CITATION_FIELDS)} citation fields present"
                     if metadata.get("exists") else "No CITATION.cff found",
            "detail": ", ".join(present) if present else None,
            "passing": len(present) >= _MIN_CITATION_FIELDS,
        }

        score = sum(1 for s in sub.values() if s["passing"])
        return {
            "score": score,
            "max_score": len(sub),
            "percentage": round(score / len(sub) * 100, 2),
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
