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
from typing import Any, Dict, List, Optional, Tuple

import httpx

from collectors.sustainability.base import GitHubCollectorBase

logger = logging.getLogger(__name__)

# Places a project can conduct decision-making in the open. Grouped so any
# variant counts once.
_DECISION_PATHS = {
    "Roadmap": [
        "ROADMAP.md", "ROADMAP", "docs/roadmap.md", "doc/roadmap.md",
        ".github/ROADMAP.md",
    ],
    "Meeting notes": [
        "meetings", "docs/meetings", "MEETINGS.md", "doc/meetings",
        "docs/meeting-notes", "notes/meetings",
    ],
    "Decision records": [
        "docs/adr", "adr", "docs/decisions", "doc/adr", "DECISIONS.md",
        "docs/architecture-decisions",
    ],
    "Governance document": [
        "GOVERNANCE.md", "docs/GOVERNANCE.md", ".github/GOVERNANCE.md",
    ],
}

# Repository features that expose discussion and documentation publicly.
_PUBLIC_CHANNELS = {
    "has_discussions": "GitHub Discussions",
    "has_wiki": "Wiki",
    "has_pages": "GitHub Pages",
}

# Two independent signals of open decision-making is a meaningful bar: one
# alone (a wiki nobody writes in, say) says very little.
_MIN_VISIBILITY_SIGNALS = 2


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

        async with httpx.AsyncClient(timeout=30.0) as client:
            channels, documents = await asyncio.gather(
                self._get_public_channels(client, owner, repo),
                self._find_decision_documents(client, owner, repo),
                return_exceptions=True,
            )

        if isinstance(channels, Exception):
            logger.warning(f"Channel lookup failed: {channels}")
            channels = []
        if isinstance(documents, Exception):
            logger.warning(f"Decision document scan failed: {documents}")
            documents = {"found": [], "details": {}}

        return {
            "package_name": repo_name,
            "repository": f"{owner}/{repo}",
            "timestamp": self._get_timestamp(),
            "public_channels": channels,
            "decision_documents": documents,
            "overall_score": self._calculate_score(channels, documents),
        }

    async def _get_public_channels(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> List[str]:
        """Discussions / wiki / pages flags, straight off the repository object."""
        resp = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}", headers=self.github_headers
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        return [label for flag, label in _PUBLIC_CHANNELS.items() if data.get(flag)]

    async def _find_decision_documents(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> Dict[str, Any]:
        """Roadmaps, meeting notes, decision records and governance docs."""

        async def check(label: str, paths: List[str]) -> Tuple[str, Optional[str]]:
            for path in paths:
                url = await self._check_file_exists(client, owner, repo, path)
                if url:
                    return label, url
            return label, None

        results = await asyncio.gather(
            *[check(label, paths) for label, paths in _DECISION_PATHS.items()]
        )
        found, details = [], {}
        for label, url in results:
            if url:
                found.append(label)
                details[label] = {"exists": True, "url": url}
            else:
                details[label] = {"exists": False}
        return {"found": found, "details": details}

    def _calculate_score(self, channels: List[str], documents: Dict) -> Dict[str, Any]:
        signals = list(channels) + list(documents.get("found", []))
        passing = len(signals) >= _MIN_VISIBILITY_SIGNALS

        sub: Dict[str, Dict[str, Any]] = {
            "decision_making_visibility": {
                "label": "Decision-Making Visibility",
                "value": f"{len(signals)} public channel(s)" if signals
                         else "No public decision-making channels found",
                "detail": ", ".join(signals) if signals else None,
                "passing": passing,
            }
        }
        for key, label in [
            ("chaoss_community_experience", "CHAOSS Community Experience Metrics"),
            ("response_quality_tone", "Response Quality and Tone Analysis"),
            ("communication_sentiment", "Communication Sentiment Analysis"),
            ("contributor_journey", "Contributor Journey Mapping"),
            ("language_communication", "Language and Communication Review"),
            ("leadership_representation", "Leadership Role Representation"),
        ]:
            sub[key] = {"label": label, "value": None, "passing": False, "not_collected": True}

        score = sum(1 for s in sub.values() if s.get("passing"))
        return {
            "score": score,
            "max_score": len(sub),
            "percentage": round(score / len(sub) * 100, 2),
            "sub_scores": sub,
        }

    def _empty_result(self, repo_name: str) -> Dict[str, Any]:
        return {
            "package_name": repo_name,
            "repository": "unknown",
            "timestamp": self._get_timestamp(),
            "public_channels": [],
            "decision_documents": {"found": [], "details": {}},
            "overall_score": self._calculate_score([], {"found": []}),
        }
