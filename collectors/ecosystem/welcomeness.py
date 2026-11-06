"""
Welcomeness Collector (CASS Report Section 4.2.6)

Covers the one sub-metric of the seven that repository data can answer:
Decision-Making Visibility — whether the project conducts and records its
decisions somewhere the community can see.

The other six (CHAOSS community experience, response tone, sentiment,
contributor journey mapping, language review, leadership representation) all
need natural-language analysis of community conversations or demographic data
about maintainers, and stay uncollected.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

import httpx

from collectors.ecosystem.base import (
    COLLECTION_GAP, PUBLIC_CHANNEL_PATTERNS, PUBLIC_CHANNEL_WORDS, RepoTree, RetryingTransport, get_threshold,
)
from collectors.ecosystem.base import excerpt, readme_source
from forge.interface import Forge

logger = logging.getLogger(__name__)

# Places a project can conduct decision-making in the open. Grouped so any
# variant counts once.
_DECISION_PATHS = {
    "Decision records": [
        "docs/adr", "adr", "docs/decisions", "doc/adr", "DECISIONS.md",
        "docs/architecture-decisions",
    ],
    "Governance document": [
        "GOVERNANCE.md", "GOVERNANCE.rst", "docs/GOVERNANCE.md", ".github/GOVERNANCE.md",
    ],
}

# Roadmap and meeting-notes documents are matched by regex rather than a
# literal candidate list -- a project's roadmap doesn't have to be named
# exactly "roadmap.md" (CHIP-SPV: docs/Devicelib_roadmap.md, petsc:
# doc/community/roadmap.md and doc/overview/gpu_roadmap.md), and meeting
# notes don't have to live in a directory named exactly "meetings" (llvm's
# flang subproject keeps them at flang/docs/MeetingNotes/). All four
# portfolio repos the probe flagged use a name a fixed list never enumerated.
_DECISION_PATTERNS = {
    "Roadmap": r"(^|/)[\w.-]*roadmap[\w.-]*\.(md|rst|txt)$",
    "Meeting notes": r"(^|/)meeting[-_]?notes?(/|$)|(^|/)[\w.-]*meeting[-_]?notes[\w.-]*\.(md|rst|txt)$",
}

# Repository features that expose discussion and documentation publicly.
# "{forge}" is the platform's name (GitHub, GitLab).
_PUBLIC_CHANNELS = {
    "has_discussions": "{forge} Discussions",
    "has_wiki": "Wiki",
    "has_pages": "{forge} Pages",
}

# README-linked venues where a community can see decisions being made --
# the same definitions 4.2.3 counts. Help desks are support, not
# deliberation, so they're left out.
_README_CHANNELS = ("Mailing list", "Forum", "Chat (Slack/Discord/Matrix)")

# What each search above matches, in words, for the report's "looked for"
# lines. Keep in step with _DECISION_PATTERNS; the channel terms come from
# base.PUBLIC_CHANNEL_WORDS, the same source as the patterns.
_PATTERN_DESCRIPTIONS = {
    "Roadmap": ["any file named *roadmap*.md, .rst or .txt"],
    "Meeting notes": ["a meeting-notes directory", "any file named *meeting-notes*.md, .rst or .txt"],
}
_README_CHANNEL_TERMS = {
    label: ", ".join(PUBLIC_CHANNEL_WORDS[label]) for label in _README_CHANNELS
}


class WelcomenessCollector:
    """Collects decision-making visibility signals (Section 4.2.6)."""

    def __init__(self, forge: Forge):
        self.forge = forge

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        repo_name = package.get("name", "Unknown")
        ref = self.forge.extract_ref(package.get("repo_url", ""))
        if not ref:
            logger.error(f"Could not extract a repo reference from {package.get('repo_url')}")
            return self._empty_result(repo_name)

        logger.info(f"Collecting welcomeness metrics for {repo_name}")

        async with httpx.AsyncClient(timeout=30.0, transport=RetryingTransport()) as client:
            results = await asyncio.gather(
                self._get_public_channels(client, ref),
                RepoTree.fetch(client, self.forge, ref),
                return_exceptions=True,
            )

        if isinstance(results[0], Exception):
            logger.warning(f"COLLECTION-GAP category=public_channels reason=exception:{results[0]!r}")
            channels, channels_gap, looked = [], True, {}
        else:
            channels, channels_gap, looked = results[0]

        tree = COLLECTION_GAP if isinstance(results[1], Exception) else results[1]
        if isinstance(results[1], Exception):
            logger.warning(f"COLLECTION-GAP category=decision_documents reason=exception:{results[1]!r}")
        documents = self._find_decision_documents(tree)
        if looked.get("readme"):
            looked["readme_source"] = readme_source(tree)

        return {
            "package_name": repo_name,
            "repository": ref,
            "timestamp": self.forge.get_timestamp(),
            "public_channels": channels,
            "decision_documents": documents,
            "overall_score": self._calculate_score(channels, documents, channels_gap, looked),
        }

    async def _get_public_channels(
        self, client: httpx.AsyncClient, ref: str
    ) -> tuple:
        """Discussions / wiki / pages flags off the repository object, plus
        channels the README links to.

        The wiki counts only if it has pages: has_wiki is on by default, so
        an empty wiki was credited as a decision-making channel (AMReX),
        the same false positive #68 removed from 4.2.3. A public mailing
        list (SUNDIALS) wasn't counted at all.

        Returns (channels, saw_gap, looked). `looked` says which of these
        checks actually ran, for the report: "settings" (the flags were
        read), "wiki_empty" (a wiki enabled with no pages), "readme"
        ("read" or "absent"; missing if it gapped) and "readme_matches"
        (channel -> the README text that matched).
        """
        data = await self.forge.repo_info(client, ref)
        if data is COLLECTION_GAP:
            return [], True, {}
        if data is None:
            return [], False, {}
        looked: Dict[str, Any] = {"settings": True}
        flags = dict(data)
        if flags.get("has_wiki"):
            flags["has_wiki"] = await self.forge.wiki_has_content(client, ref)
            looked["wiki_empty"] = not flags["has_wiki"]
        channels = [label.format(forge=self.forge.display_name)
                    for flag, label in _PUBLIC_CHANNELS.items() if flags.get(flag)]

        text = await self.forge.readme(client, ref)
        if text is None:
            looked["readme"] = "absent"
        elif isinstance(text, str):
            looked["readme"] = "read"
            looked["readme_matches"] = {}
            for label in _README_CHANNELS:
                match = PUBLIC_CHANNEL_PATTERNS[label].search(text)
                if match:
                    channels.append(label)
                    looked["readme_matches"][label] = excerpt(text, match, None)
        return channels, False, looked

    def _find_decision_documents(self, tree) -> Dict[str, Any]:
        """Roadmaps, meeting notes, decision records and governance docs,
        matched against a RepoTree (case-insensitive, files or directories)
        rather than probed one literal path at a time -- see
        METRIC_BLIND_SPOTS.md class F1/F2.
        """
        found, not_collected, details = [], [], {}

        for label, paths in _DECISION_PATHS.items():
            if tree is COLLECTION_GAP:
                not_collected.append(label)
                details[label] = {"not_collected": True}
                continue
            path = tree.match(paths)
            if path:
                found.append(label)
                details[label] = {"exists": True, "url": tree.match_url([path]), "path": path}
            else:
                details[label] = {"exists": False}

        for label, pattern in _DECISION_PATTERNS.items():
            if tree is COLLECTION_GAP:
                not_collected.append(label)
                details[label] = {"not_collected": True}
                continue
            hits = tree.find(pattern)
            if hits:
                found.append(label)
                details[label] = {"exists": True, "url": tree.url_for(hits[0]), "path": hits[0]}
            else:
                details[label] = {"exists": False}

        return {"found": found, "not_collected": not_collected, "details": details}

    def _visibility_evidence(self, channels: List[str], documents: Dict, looked: Dict) -> Dict[str, Any]:
        """Where each decision-making signal was found or looked for.

        Channels from repository settings are named in the row's detail
        already, so only their absence is added; a check that didn't run
        (a gapped settings or README read, a gapped tree) claims nothing.
        """
        found, missing, searched = [], [], []
        readme = looked.get("readme_source") or {"path": "README", "url": None}

        for label, excerpt in looked.get("readme_matches", {}).items():
            found.append({"text": label, **readme, "quote": excerpt})
        if looked.get("settings"):
            forge = self.forge.display_name
            # GitLab has no Discussions feature to enable.
            if self.forge.platform == "github" and f"{forge} Discussions" not in channels:
                missing.append({"text": f"{forge} Discussions (not enabled)"})
            if looked.get("wiki_empty"):
                missing.append({"text": "Wiki with pages (the wiki is enabled but has none)"})
            elif "Wiki" not in channels:
                missing.append({"text": "Wiki (not enabled)"})
            if f"{forge} Pages" not in channels:
                missing.append({"text": f"{forge} Pages (not enabled)"})
        if looked.get("readme") == "absent":
            missing.append({"text": "a README to read for mailing list, forum and chat links"})
        elif looked.get("readme") == "read":
            for label in _README_CHANNELS:
                if label not in looked.get("readme_matches", {}):
                    missing.append({"text": f"{label} in {readme['path']} "
                                            f"(looked for: {_README_CHANNEL_TERMS[label]})"})

        details = documents.get("details", {})
        for label in list(_DECISION_PATHS) + list(_DECISION_PATTERNS):
            info = details.get(label, {})
            if info.get("exists"):
                found.append({"text": label, "path": info.get("path"), "url": info.get("url")})
            elif info.get("exists") is False:
                missing.append({"text": label})
                places = _DECISION_PATHS.get(label) or _PATTERN_DESCRIPTIONS[label]
                searched.append({"text": f"{label} looked for (paths in any capitalization)",
                                 "paths": [{"path": p} for p in places]})
        return {"found": found, "missing": missing, "searched": searched}

    def _calculate_score(
        self, channels: List[str], documents: Dict, channels_gap: bool = False,
        looked: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        signals = list(channels) + list(documents.get("found", []))
        passing = len(signals) >= get_threshold("4.2.6", "Decision-Making Visibility")

        # A below-threshold count built on a gap isn't confirmed -- a gapped
        # channel lookup or decision-document candidate could have supplied
        # the missing signal. A count that already clears the threshold from
        # confirmed data stands regardless.
        visibility_entry: Dict[str, Any] = {
            "label": "Decision-Making Visibility",
            "value": f"{len(signals)} public channel(s)" if signals
                     else "No public decision-making channels found",
            "detail": ", ".join(signals) if signals else None,
            "passing": passing,
            "evidence": self._visibility_evidence(channels, documents, looked or {}),
        }
        if not passing and (channels_gap or documents.get("not_collected")):
            visibility_entry["not_collected"] = True

        sub: Dict[str, Dict[str, Any]] = {"decision_making_visibility": visibility_entry}
        for key, label in [
            ("chaoss_community_experience", "CHAOSS Community Experience Metrics"),
            ("response_quality_tone", "Response Quality and Tone Analysis"),
            ("communication_sentiment", "Communication Sentiment Analysis"),
            ("contributor_journey", "Contributor Journey Mapping"),
            ("language_communication", "Language and Communication Review"),
            ("leadership_representation", "Leadership Role Representation"),
        ]:
            sub[key] = {"label": label, "value": None, "passing": False, "not_collected": True}

        scorable = {k: v for k, v in sub.items() if not v.get("not_collected")}
        score = sum(1 for s in scorable.values() if s.get("passing"))
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
            "timestamp": self.forge.get_timestamp(),
            "public_channels": [],
            "decision_documents": {"found": [], "not_collected": [], "details": {}},
            "overall_score": self._calculate_score([], {"found": []}),
        }
