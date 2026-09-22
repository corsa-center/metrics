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
from typing import Any, Dict, List

import httpx

from collectors.ecosystem.base import COLLECTION_GAP, GitHubCollectorBase, RepoTree, RetryingTransport, get_threshold

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
_PUBLIC_CHANNELS = {
    "has_discussions": "GitHub Discussions",
    "has_wiki": "Wiki",
    "has_pages": "GitHub Pages",
}


class WelcomenessCollector(GitHubCollectorBase):
    """Collects decision-making visibility signals (Section 4.2.6)."""

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        repo_name = package.get("name", "Unknown")
        owner_repo = self._extract_owner_repo(package.get("repo_url", ""))
        if not owner_repo:
            logger.error(f"Could not extract owner/repo from {package.get('repo_url')}")
            return self._empty_result(repo_name)

        owner, repo = owner_repo
        logger.info(f"Collecting welcomeness metrics for {repo_name}")

        async with httpx.AsyncClient(timeout=30.0, transport=RetryingTransport()) as client:
            results = await asyncio.gather(
                self._get_public_channels(client, owner, repo),
                RepoTree.fetch(client, self.github_headers, owner, repo),
                return_exceptions=True,
            )

        if isinstance(results[0], Exception):
            logger.warning(f"COLLECTION-GAP category=public_channels reason=exception:{results[0]!r}")
            channels, channels_gap = [], True
        else:
            channels, channels_gap = results[0]

        tree = COLLECTION_GAP if isinstance(results[1], Exception) else results[1]
        if isinstance(results[1], Exception):
            logger.warning(f"COLLECTION-GAP category=decision_documents reason=exception:{results[1]!r}")
        documents = self._find_decision_documents(tree)

        return {
            "package_name": repo_name,
            "repository": f"{owner}/{repo}",
            "timestamp": self._get_timestamp(),
            "public_channels": channels,
            "decision_documents": documents,
            "overall_score": self._calculate_score(channels, documents, channels_gap),
        }

    async def _get_public_channels(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> tuple:
        """Discussions / wiki / pages flags, straight off the repository object.

        Returns (channels, saw_gap).
        """
        data = await self._github_get(client, f"https://api.github.com/repos/{owner}/{repo}")
        if data is COLLECTION_GAP:
            return [], True
        if data is None:
            return [], False
        return [label for flag, label in _PUBLIC_CHANNELS.items() if data.get(flag)], False

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
            url = tree.match_url(paths)
            if url:
                found.append(label)
                details[label] = {"exists": True, "url": url}
            else:
                details[label] = {"exists": False}

        for label, pattern in _DECISION_PATTERNS.items():
            if tree is COLLECTION_GAP:
                not_collected.append(label)
                details[label] = {"not_collected": True}
                continue
            url = tree.find_url(pattern)
            if url:
                found.append(label)
                details[label] = {"exists": True, "url": url}
            else:
                details[label] = {"exists": False}

        return {"found": found, "not_collected": not_collected, "details": details}

    def _calculate_score(
        self, channels: List[str], documents: Dict, channels_gap: bool = False
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
            "timestamp": self._get_timestamp(),
            "public_channels": [],
            "decision_documents": {"found": [], "not_collected": [], "details": {}},
            "overall_score": self._calculate_score([], {"found": []}),
        }
