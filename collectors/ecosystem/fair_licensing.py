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
import logging
import re
from typing import Any, Dict, List, Optional

import httpx
import yaml

from collectors.ecosystem.base import COLLECTION_GAP, RepoTree, RetryingTransport, excerpt, get_threshold
from forge.interface import Forge

logger = logging.getLogger(__name__)

# GitHub reports these when it cannot match the text to a known license.
_UNCLASSIFIED = {None, "", "NOASSERTION", "Other", "unknown"}

# License families recoverable from prose, ordered most specific first so
# "3-clause BSD" is not swallowed by a bare "BSD" match.
_LICENSE_TEXT_PATTERNS = [
    ("BSD-3-Clause", r"\b(?:3[- ]clause BSD|BSD[- ]3(?:[- ]clause)?|new BSD|modified BSD)\b"),
    ("BSD-2-Clause", r"\b(?:2[- ]clause BSD|BSD 2[- ]clause|simplified BSD)\b"),
    ("Apache-2.0", r"\bApache Licen[sc]e,? Version 2\.0\b"),
    ("MIT", r"\bMIT Licen[sc]e\b"),
    ("LGPL", r"\bGNU Lesser General Public Licen[sc]e\b"),
    ("GPL", r"\bGNU General Public Licen[sc]e\b"),
    ("MPL-2.0", r"\bMozilla Public Licen[sc]e,? (?:Version )?2\.0\b"),
    ("BSD", r"\bBSD Licen[sc]e\b"),
]

# The operative clauses of the standard licences, for text that reproduces a
# licence body without ever naming it. AMReX's LICENSE is verbatim
# BSD-3-Clause with "(1)"-style numbering and never says "BSD", so GitHub
# returns NOASSERTION and the name patterns above find nothing. Every phrase
# in a family must appear (matched against whitespace-collapsed text, since
# licences wrap mid-clause); ordered most specific first so BSD-3's extra
# endorsement clause is checked before BSD-2's subset.
_LICENSE_CLAUSE_FINGERPRINTS = [
    ("BSD-3-Clause", [
        r"redistributions of source code must retain the above copyright notice",
        r"redistributions in binary form must reproduce the above copyright notice",
        r"neither the name of .{1,200}? may be used to endorse or promote products derived",
    ]),
    ("BSD-2-Clause", [
        r"redistributions of source code must retain the above copyright notice",
        r"redistributions in binary form must reproduce the above copyright notice",
    ]),
    ("MIT", [
        r"permission is hereby granted, free of charge, to any person obtaining a copy",
        r"the above copyright notice and this permission notice shall be included",
    ]),
    ("ISC", [
        r"permission to use, copy, modify, and(?:/or)? distribute this software for any purpose "
        r"with or without fee is hereby granted",
    ]),
]

# SPDX ids a project may declare in CITATION.cff, mapped to the family the
# rest of the pipeline reasons about. Limited to OSI-approved licences: the
# dashboard treats every resolved family as OSI-approved, so an unlisted or
# LicenseRef-* declaration must stay unresolved rather than pass that row.
_DECLARED_SPDX_FAMILIES = [
    (r"BSD-3-Clause(?:-LBNL)?", "BSD-3-Clause"),
    (r"BSD-2-Clause", "BSD-2-Clause"),
    (r"Apache-2\.0", "Apache-2.0"),
    (r"MIT", "MIT"),
    (r"ISC", "ISC"),
    (r"LGPL-(?:2\.1|3\.0)(?:-only|-or-later|\+)?", "LGPL"),
    (r"GPL-(?:2\.0|3\.0)(?:-only|-or-later|\+)?", "GPL"),
    (r"MPL-2\.0", "MPL-2.0"),
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

# Recent tags searched for a version number when there are no releases.
_TAGS_SAMPLED = 20


_CITATION_PATHS = ["CITATION.cff"]
_CODEMETA_PATHS = ["codemeta.json"]
_ZENODO_PATHS = [".zenodo.json", "zenodo.json"]

# Fields a citation record needs before it is genuinely reusable metadata.
_CITATION_FIELDS = ["title", "authors", "version", "license", "repository-code", "doi"]

# Root-level citation files that may carry BibTeX instead of CFF
# (CITATION.bib, CITATIONS.md with ```bibtex blocks, a bare CITATION file).
_BIBTEX_CITATION_FILE = r"^(?:citations?|citing|cite)(?:\.(?:bib|md|txt|rst))?$"
_BIBTEX_ENTRY = re.compile(r"@\w+\s*\{")
_README_CITATION_HEADING = re.compile(
    r"^\s{0,3}#{1,6}\s+(?:how to cite|citing|cite|citation|citations)\b.*$", re.I | re.M)
_BIBTEX_FIELD = re.compile(r"(?i)(?<![\w-])(title|author|version|license|doi|url|repository)\s*=\s*[{\"]?([^,}\"\n]*)")
_BIBTEX_TO_CITATION_FIELD = {
    "title": "title", "author": "authors", "version": "version",
    "license": "license", "doi": "doi",
}


def _excerpt(text: str, match: Optional["re.Match"]) -> Optional[str]:
    """The text around `match`, or None without one."""
    return excerpt(text, match, None) if match else None


class FairLicensingCollector:
    """Collects FAIR compliance and license-exception signals (Section 4.2.2)."""

    def __init__(self, forge: Forge):
        self.forge = forge

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        repo_name = package.get("name", "Unknown")
        ref = self.forge.extract_ref(package.get("repo_url", ""))
        if not ref:
            logger.error(f"Could not extract a repo reference from {package.get('repo_url')}")
            return self._empty_result(repo_name)

        logger.info(f"Collecting FAIR and licensing detail for {repo_name}")

        async with httpx.AsyncClient(timeout=30.0, transport=RetryingTransport()) as client:
            tree = await RepoTree.fetch(client, self.forge, ref)
            results = await asyncio.gather(
                self._get_license(client, ref),
                self._get_citation(client, ref, tree),
                self._has_releases(client, ref),
                return_exceptions=True,
            )

        names = ["license", "citation", "releases"]
        defaults = [
            ({"spdx_id": None, "text": ""}, True),
            ({}, True),
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
        (releases, releases_gap) = unpacked[2]
        has_codemeta, codemeta_gap = self._any_exists(tree, _CODEMETA_PATHS)
        has_zenodo, zenodo_gap = self._any_exists(tree, _ZENODO_PATHS)

        exceptions = self._analyze_license_text(
            license_data, license_gap, declared=(citation or {}).get("license"),
        )
        metadata = self._analyze_citation(citation, citation_gap)
        fair = self._assess_fair(
            exceptions, metadata, has_codemeta, has_zenodo, releases,
            codemeta_gap, zenodo_gap, releases_gap,
        )
        bibtex, bibtex_read = None, None
        if not metadata.get("exists") and tree is not COLLECTION_GAP:
            bibtex_read = []
            async with httpx.AsyncClient(timeout=30.0, transport=RetryingTransport()) as client:
                bibtex = await self._get_bibtex_citation(client, ref, tree, read=bibtex_read)

        overall = self._calculate_score(exceptions, metadata, fair, bibtex)
        # Each row's evidence, for the per-package report (#99).
        citation_src = self._located(tree, _CITATION_PATHS)
        # Only text that came back was read; GitLab's detection carries none.
        license_src = ({"path": license_data.get("file_path"), "url": license_data.get("url")}
                       if license_data.get("text") else {})
        evidence = {
            "fair4rs_assessment": self._fair_evidence(
                fair, exceptions, metadata, releases, releases_gap, tree is COLLECTION_GAP,
                citation_src, self._located(tree, _CODEMETA_PATHS),
                self._located(tree, _ZENODO_PATHS),
                citation_src if exceptions.get("resolved_via") == "citation" else license_src,
            ),
            "license_exception_handling": self._exception_evidence(
                exceptions, license_data, license_src, citation, citation_gap, citation_src,
            ),
            "fair_metadata": self._metadata_evidence(
                metadata, citation, citation_gap, citation_src, bibtex, bibtex_read,
            ),
        }
        for key, ev in evidence.items():
            overall["sub_scores"][key]["evidence"] = ev

        return {
            "package_name": repo_name,
            "repository": ref,
            "timestamp": self.forge.get_timestamp(),
            "license_exceptions": exceptions,
            "citation_metadata": metadata,
            "bibtex_citation": bibtex,
            "fair": fair,
            "overall_score": overall,
        }

    # ------------------------------------------------------------------ fetch

    async def _get_license(
        self, client: httpx.AsyncClient, ref: str
    ) -> tuple:
        """SPDX id from the API plus the raw licence text. Returns (data, saw_gap)."""
        data = await self.forge.license(client, ref)
        if data is COLLECTION_GAP:
            return {"spdx_id": None, "text": ""}, True
        if data is None:
            return {"spdx_id": None, "text": ""}, False
        return {
            "spdx_id": data["spdx_id"],
            "name": data["name"],
            "text": data["text"],
            "file_path": data.get("file_path"),
            "url": data.get("html_url"),
        }, False

    async def _get_citation(
        self, client: httpx.AsyncClient, ref: str, tree
    ) -> tuple:
        """Parsed CITATION.cff, or an empty dict if absent or unparseable.

        Returns (citation, saw_gap). Resolved against a RepoTree first so a
        differently-cased file (Lab-Notebooks/CodeScribe ships citation.cff,
        not CITATION.cff) is still found -- see METRIC_BLIND_SPOTS.md class F1.
        """
        if tree is COLLECTION_GAP:
            return {}, True
        path = tree.match(_CITATION_PATHS)
        if path is None:
            return {}, False
        text = await self.forge.file_content(client, ref, path)
        if text is COLLECTION_GAP:
            return {}, True
        if text is None:
            return {}, False
        try:
            parsed = yaml.safe_load(text)
            if isinstance(parsed, dict):
                return parsed, False
        except Exception as e:
            logger.debug(f"Could not parse {path}: {e}")
        return {}, False

    def _any_exists(self, tree, paths: List[str]) -> tuple:
        """Returns (found, saw_gap)."""
        if tree is COLLECTION_GAP:
            return False, True
        return bool(tree.match(paths)), False

    async def _has_releases(
        self, client: httpx.AsyncClient, ref: str
    ) -> tuple:
        """Returns (has_releases, saw_gap). Version tags count too: many
        projects publish versions as tags without release objects."""
        data = await self.forge.releases(client, ref, per_page=1)
        if data is COLLECTION_GAP:
            return False, True
        if data:
            return True, False
        tags = await self.forge.tags(client, ref, per_page=_TAGS_SAMPLED)
        if tags is COLLECTION_GAP:
            return False, True
        return any(re.search(r"\d+\.\d+", t.get("name", "")) for t in tags or []
                   if isinstance(t, dict)), False

    # ---------------------------------------------------------------- analyze

    def _analyze_license_text(
        self, license_data: Dict[str, Any], saw_gap: bool = False, declared: Any = None,
    ) -> Dict[str, Any]:
        """Recover a license family the API could not name, and flag extra terms.

        Tried in order of how directly the evidence names the licence: a
        licence named in its own text, then the standard clauses reproduced
        without a name, then the SPDX id the project declares in CITATION.cff.
        """
        spdx = license_data.get("spdx_id")
        text = license_data.get("text") or ""
        classified = spdx not in _UNCLASSIFIED

        resolved, resolved_via = None, None
        if not classified:
            if text:
                resolved = self._license_named_in_text(text)
                resolved_via = "text" if resolved else None
            if not resolved and text:
                resolved = self._license_from_clauses(text)
                resolved_via = "clauses" if resolved else None
            if not resolved:
                resolved = self._license_from_declaration(declared)
                resolved_via = "citation" if resolved else None

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
            "resolved_via": resolved_via,
            "exception_markers": markers,
            "identified": identified,
        }
        # An unidentified result built on a gap isn't a confirmed "no
        # license" -- an identified one stands regardless, since it came
        # from text/SPDX data that did come back.
        if not identified and saw_gap:
            result["not_collected"] = True
        return result

    @classmethod
    def _license_named_in_text(cls, text: str) -> Optional[str]:
        hit = cls._license_name_match(text)
        return hit[0] if hit else None

    @staticmethod
    def _license_name_match(text: str) -> Optional[tuple]:
        """(family, match) for the first license named in `text`, or None."""
        for name, pattern in _LICENSE_TEXT_PATTERNS:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                return name, m
        return None

    @staticmethod
    def _license_from_clauses(text: str) -> Optional[str]:
        flat = re.sub(r"\s+", " ", text).lower()
        # "(1)" / "1." / "*" list markers sit between clauses, not inside
        # them, so collapsing whitespace is enough for the phrases to match.
        for name, phrases in _LICENSE_CLAUSE_FINGERPRINTS:
            if all(re.search(p, flat) for p in phrases):
                return name
        return None

    @staticmethod
    def _license_from_declaration(declared: Any) -> Optional[str]:
        # CITATION.cff allows a single id or a list; a list is an OR of
        # licences, so any recognised entry is enough.
        ids = declared if isinstance(declared, list) else [declared]
        for spdx_id in ids:
            if not isinstance(spdx_id, str):
                continue
            for pattern, family in _DECLARED_SPDX_FAMILIES:
                if re.fullmatch(pattern, spdx_id.strip(), re.IGNORECASE):
                    return family
        return None

    async def _get_bibtex_citation(
        self, client: httpx.AsyncClient, ref: str, tree, read: Optional[List] = None,
    ) -> Dict[str, Any]:
        """Citation fields carried by BibTeX in a root-level citation file,
        or None if there's no such file or it has no BibTeX entries.

        Scored separately from CITATION.cff and not fed into FAIR4RS: the
        entries usually cite papers, so their DOI identifies a paper rather
        than the software. Each file actually read is appended to `read`, as
        {"path", "url"}, so a report can say where BibTeX was looked for.
        """
        read = [] if read is None else read
        paths = [p for p in tree.find(_BIBTEX_CITATION_FILE) if "/" not in p]
        for path in paths:
            text = await self.forge.file_content(client, ref, path)
            if isinstance(text, str):
                read.append({"path": path, "url": tree.url_for(path)})
            if not text:
                continue
            if not _BIBTEX_ENTRY.search(text):
                continue
            return {"path": path, "url": tree.url_for(path),
                    **self._analyze_bibtex(text, self._repo_ref(ref))}
        return await self._readme_bibtex(client, ref, read)

    def _repo_ref(self, ref: str) -> str:
        """"host/owner/repo", as a BibTeX url field would contain it."""
        return f"{self.forge.host}/{ref}".lower()

    async def _readme_bibtex(
        self, client: httpx.AsyncClient, ref: str, read: Optional[List] = None,
    ) -> Dict[str, Any]:
        """BibTeX under the README's own citation heading ("Citation", "How to
        cite", "Citing"), or None. Only that section is read: BibTeX elsewhere
        in a README is usually related work, not how to cite this project."""
        text = await self.forge.readme(client, ref)
        if isinstance(text, str) and read is not None:
            read.append({"path": "README (under a Citation, Citing or How to cite heading)"})
        if not text:
            return None
        m = _README_CITATION_HEADING.search(text)
        if not m:
            return None
        section = text[m.end():]
        nxt = re.search(r"^\s{0,3}#{1,6}\s", section, re.M)
        section = section[:nxt.start()] if nxt else section
        if not _BIBTEX_ENTRY.search(section):
            return None
        return {"path": "README citation section",
                **self._analyze_bibtex(section, self._repo_ref(ref))}

    @staticmethod
    def _analyze_bibtex(text: str, repo_ref: str) -> Dict[str, Any]:
        """Which _CITATION_FIELDS appear in any BibTeX entry. A url or
        repository field counts as repository-code only if it points at
        this repository."""
        found = set()
        for name, value in _BIBTEX_FIELD.findall(text):
            name = name.lower()
            if not value.strip():
                continue
            if name in ("url", "repository"):
                if repo_ref in value.lower():
                    found.add("repository-code")
            else:
                found.add(_BIBTEX_TO_CITATION_FIELD[name])
        present = [f for f in _CITATION_FIELDS if f in found]
        return {"present": present, "missing": [f for f in _CITATION_FIELDS if f not in found]}

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

    # --------------------------------------------------------------- evidence
    # What each row found and where, or what it looked for and where, for the
    # per-package report (#99). Nothing is claimed as searched for an input
    # that gapped.

    @staticmethod
    def _located(tree, paths: List[str]) -> Dict[str, Any]:
        """The first of `paths` in the tree as {"path", "url"}, or {}."""
        if tree is COLLECTION_GAP:
            return {}
        path = tree.match(paths)
        return {"path": path, "url": tree.match_url([path])} if path else {}

    def _fair_evidence(
        self, fair: Dict, exceptions: Dict, metadata: Dict,
        releases: bool, releases_gap: bool, tree_gap: bool,
        citation_src: Dict, codemeta_src: Dict, zenodo_src: Dict, license_src: Dict,
    ) -> Dict[str, List]:
        """What satisfied each principle, and for each that certainly
        failed, what would satisfy it."""
        principles = fair.get("principles", {})
        gaps = fair.get("principle_gaps", {})

        found = []
        if "doi" in metadata.get("present", []):
            found.append({"text": "DOI (Findable)", **citation_src})
        if zenodo_src:
            found.append({"text": "Zenodo metadata (Findable)", **zenodo_src})
        if exceptions.get("identified"):
            name = (exceptions.get("api_spdx") if exceptions.get("api_classified")
                    else exceptions.get("resolved_from_text"))
            found.append({"text": f"{name} license (Accessible)", **license_src})
        if metadata.get("exists"):
            found.append({"text": "Citation metadata (Interoperable)", **citation_src})
        if codemeta_src:
            found.append({"text": "CodeMeta metadata (Interoperable)", **codemeta_src})
        if principles.get("Reusable"):
            found.append({"text": "A release or version tag, with the license (Reusable)"})

        def failed(principle: str) -> bool:
            return principle in principles and not principles[principle] and not gaps.get(principle)

        missing = []
        if failed("Findable"):
            missing.append({"text": "Findable: a DOI in CITATION.cff, or a .zenodo.json file"})
        if failed("Accessible"):
            missing.append({"text": "Accessible: a license that can be identified "
                                    "(see License Exception Handling)"})
        if failed("Interoperable"):
            missing.append({"text": "Interoperable: CITATION.cff or codemeta.json"})
        if failed("Reusable"):
            needs = []
            if not exceptions.get("identified") and not exceptions.get("not_collected"):
                needs.append("a license that can be identified")
            if not releases and not releases_gap:
                needs.append(f"a release, or a version tag like 1.2 among the "
                             f"{_TAGS_SAMPLED} most recent tags")
            missing.append({"text": "Reusable: " + " and ".join(needs)})

        searched = []
        if not tree_gap and (failed("Findable") or failed("Interoperable")):
            searched.append({
                "text": "Metadata files looked for at the repository root (any capitalization)",
                "paths": [{"path": p} for p in _CITATION_PATHS + _CODEMETA_PATHS + _ZENODO_PATHS],
            })
        return {"found": found, "missing": missing, "searched": searched}

    def _exception_evidence(
        self, exceptions: Dict, license_data: Dict, license_src: Dict,
        citation: Dict, citation_gap: bool, citation_src: Dict,
    ) -> Dict[str, List]:
        """The text that identified the license and each extra term, or,
        for a license nothing identified, what each method looked for."""
        text = license_data.get("text") or ""
        declared = (citation or {}).get("license")
        if isinstance(declared, list):
            declared = ", ".join(str(d) for d in declared)
        family = exceptions.get("resolved_from_text")
        via = exceptions.get("resolved_via")

        found = []
        if exceptions.get("api_classified"):
            if license_src:
                found.append({"text": "License text", **license_src})
        elif via == "text":
            hit = self._license_name_match(text)
            found.append({"text": f"{family} named", **license_src,
                          "quote": _excerpt(text, hit[1] if hit else None)})
        elif via == "clauses":
            # The endorsement clause is BSD-3's distinguishing one; for the
            # others the last phrase is as telling as any.
            phrase = dict(_LICENSE_CLAUSE_FINGERPRINTS)[family][-1]
            m = re.search(phrase, re.sub(r"\s+", " ", text), re.IGNORECASE)
            found.append({"text": f"The standard {family} clauses", **license_src,
                          "quote": m.group(0) if m else None})
        elif via == "citation":
            found.append({"text": "License declaration", **citation_src,
                          "quote": f"license: {declared}"})

        markers = exceptions.get("exception_markers", [])
        for label, pattern in _EXCEPTION_MARKERS:
            if label in markers:
                found.append({"text": label, **license_src,
                              "quote": _excerpt(text, re.search(pattern, text, re.IGNORECASE))})
        if "Multiple copyright holders" in markers:
            count = len(_COPYRIGHT_LINE.findall(text))
            found.append({"text": f"Multiple copyright holders ({count} copyright lines)",
                          **license_src})

        missing = []
        if not exceptions.get("identified") and not exceptions.get("not_collected"):
            host = getattr(self.forge, "display_name", None) or "the forge"
            if "file_path" not in license_data:
                missing.append({"text": f"license text from {host}'s license detection "
                                        "(it detected no license)"})
            elif text:
                path = license_data.get("file_path")
                names = ", ".join(dict.fromkeys(n for n, _ in _LICENSE_TEXT_PATTERNS))
                clauses = [n for n, _ in _LICENSE_CLAUSE_FINGERPRINTS]
                missing.append({"text": f"a license name in {path} (looked for: {names})"})
                missing.append({"text": f"the standard clauses of {', '.join(clauses[:-1])} "
                                        f"or {clauses[-1]} in {path}"})
            if not citation_gap:
                cff = citation_src.get("path", "CITATION.cff")
                if not citation_src:
                    reason = "there is no CITATION.cff"
                elif not citation:
                    reason = f"{cff} could not be read or parsed"
                elif not declared:
                    reason = f"{cff} has no license field"
                else:
                    reason = f"{cff} declares “{declared}”, which isn't one this check accepts"
                missing.append({"text": f"an OSI-approved SPDX license id declared in "
                                        f"CITATION.cff ({reason})"})
        return {"found": found, "missing": missing}

    def _metadata_evidence(
        self, metadata: Dict, citation: Dict, citation_gap: bool, citation_src: Dict,
        bibtex: Optional[Dict], bibtex_read: Optional[List],
    ) -> Dict[str, List]:
        """The citation file and the fields it lacks, or, without one, the
        BibTeX used instead or where BibTeX was looked for."""
        found, missing, searched = [], [], []
        if metadata.get("exists"):
            cff = citation_src.get("path", "CITATION.cff")
            found.append({"text": "Citation metadata", **citation_src})
            for field in metadata.get("missing", []):
                if field == "doi":
                    field = "doi (top level, or an identifiers entry of type doi)"
                missing.append({"text": f"{field} in {cff}"})
            return {"found": found, "missing": missing}

        if not citation_gap:
            if citation_src:
                missing.append({"text": f"readable citation metadata ({citation_src['path']} "
                                        "could not be read or parsed)"})
            else:
                missing.append({"text": "CITATION.cff (looked for at the repository root, "
                                        "any capitalization)"})
        if bibtex:
            found.append({"text": "BibTeX entry", "path": bibtex.get("path"),
                          "url": bibtex.get("url")})
            missing += [{"text": f"{field} in the BibTeX"} for field in bibtex.get("missing", [])]
        elif bibtex_read:
            searched.append({"text": "BibTeX looked for in", "paths": bibtex_read})
        return {"found": found, "missing": missing, "searched": searched}

    # ---------------------------------------------------------------- scoring

    def _calculate_score(
        self, exceptions: Dict, metadata: Dict, fair: Dict, bibtex: Dict = None
    ) -> Dict[str, Any]:
        sub: Dict[str, Dict[str, Any]] = {}

        satisfied = fair.get("satisfied", [])
        fair_passing = fair.get("count", 0) >= get_threshold("4.2.2", "Automated FAIR4RS Assessment")
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
            source = {
                "clauses": "licence clauses match",
                "citation": "CITATION.cff declares",
            }.get(exceptions.get("resolved_via"), "text identifies")
            value = (f"Reported as \"{exceptions.get('api_spdx') or 'none'}\"; "
                     f"{source} {exceptions['resolved_from_text']}")
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
        if metadata.get("exists"):
            meta_value = f"{len(present)}/{len(_CITATION_FIELDS)} citation fields present"
        elif bibtex:
            present = bibtex["present"]
            meta_value = (f"{len(present)}/{len(_CITATION_FIELDS)} citation fields present "
                          f"(BibTeX in {bibtex['path']}; no CITATION.cff)")
        else:
            meta_value = "No CITATION.cff found"
        meta_passing = len(present) >= get_threshold("4.2.2", "FAIR Metadata Assessment")
        meta_entry: Dict[str, Any] = {
            "label": "FAIR Metadata Assessment",
            "value": meta_value,
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
            "timestamp": self.forge.get_timestamp(),
            "license_exceptions": exceptions,
            "citation_metadata": metadata,
            "fair": fair,
            "overall_score": self._calculate_score(exceptions, metadata, fair),
        }
