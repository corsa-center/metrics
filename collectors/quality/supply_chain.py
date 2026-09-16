"""
Software Supply Chain Integrity Collector (CASS Report Section 4.3.8)

Checks the transparency and verifiability of a project's build and
distribution pipeline:
  - SBOM Detection   : an SPDX/CycloneDX bill of materials, committed to the
                        repository root or published as a release asset
  - Build Provenance : a SLSA / in-toto attestation published alongside a
                        release

Dependency Vulnerability Posture and Dependency Freshness (libyears) are not
collected here: the former needs Dependabot alert access this survey doesn't
have on third-party repos, and the latter needs a machine-readable dependency
manifest most HPC C/C++ projects don't publish. Per CASS §3.5, both are
reported as not-yet-collected rather than scored zero.

Badge and Scorecard level are not fetched here either — they're already
collected by openssf_badge.py / openssf_scorecard.py (Section 4.2) and are
surfaced alongside this section by the dashboard transform rather than
re-fetched.
"""

import asyncio
import httpx
import logging
from typing import Any, Dict, List, Optional

from collectors.ecosystem.base import COLLECTION_GAP, GitHubCollectorBase, RetryingTransport

logger = logging.getLogger(__name__)

_SBOM_ROOT_FILES = [
    "sbom.spdx.json", "SBOM.spdx.json", "sbom.json", "SBOM.json",
    "bom.json", "bom.xml", ".github/sbom.spdx.json",
]

# Filename fragments matched against release-asset names (lower-cased).
_SBOM_ASSET_HINTS = ["sbom", "spdx", "cyclonedx", "bom.xml", "bom.json", ".cdx.json"]
_PROVENANCE_ASSET_HINTS = [
    "intoto", "in-toto", "provenance", "slsa", ".sigstore", "attestation",
]

_RELEASES_SAMPLE = 5


class SupplyChainCollector(GitHubCollectorBase):
    """Collects supply-chain transparency indicators (CASS Report Section 4.3.8)."""

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        repo_name = package.get("name", "Unknown")
        repo_url = package.get("repo_url", "")

        owner_repo = self._extract_owner_repo(repo_url)
        if not owner_repo:
            logger.error(f"Could not extract owner/repo from {repo_url}")
            return self._empty_result(repo_name)

        owner, repo = owner_repo
        logger.info(f"Collecting supply chain metrics for {owner}/{repo}")

        async with httpx.AsyncClient(timeout=30.0, transport=RetryingTransport()) as client:
            (release_assets, assets_gap), (root_sbom, root_gap) = await asyncio.gather(
                self._fetch_release_assets(client, owner, repo),
                self._check_root_sbom(client, owner, repo),
            )

        sbom = self._find_sbom(root_sbom, release_assets, root_gap or assets_gap)
        provenance = self._find_provenance(release_assets, assets_gap)

        sub_metrics = {
            "sbom_detection": sbom,
            "build_provenance": provenance,
            "dependency_vulnerability_posture": {
                "label": "Dependency Vulnerability Posture",
                "value": None, "passing": False, "not_collected": True,
            },
            "dependency_freshness": {
                "label": "Dependency Freshness",
                "value": None, "passing": False, "not_collected": True,
            },
        }

        overall = self._compute_overall(sub_metrics)

        return {
            "package_name": repo_name,
            "repository": f"{owner}/{repo}",
            "timestamp": self._get_timestamp(),
            "has_sbom": sbom["passing"],
            "has_build_provenance": provenance["passing"],
            "sub_metrics": sub_metrics,
            "overall_score": overall,
        }

    # ------------------------------------------------------------------ #
    # Fetching                                                             #
    # ------------------------------------------------------------------ #

    async def _check_root_sbom(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> tuple:
        """Returns (html_url_or_None, saw_gap). A gap on one candidate path
        doesn't stop the rest from being checked, but is tracked so a
        resulting "not found" can be reported as not_collected rather than
        a confirmed absence.
        """
        saw_gap = False
        for path in _SBOM_ROOT_FILES:
            html_url = await self._check_file_exists(client, owner, repo, path)
            if html_url is COLLECTION_GAP:
                saw_gap = True
                continue
            if html_url:
                return html_url, saw_gap
        return None, saw_gap

    async def _fetch_release_assets(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> tuple:
        """Returns (flat list of {name, url, release}, is_gap). An empty
        list with is_gap=False is a real, confirmed result (no releases);
        is_gap=True means the release list itself couldn't be fetched, so
        an empty list here says nothing trustworthy about SBOM/provenance
        presence.
        """
        releases = await self._github_get(
            client, f"https://api.github.com/repos/{owner}/{repo}/releases",
            params={"per_page": _RELEASES_SAMPLE},
        )
        if releases is COLLECTION_GAP:
            return [], True

        assets = []
        for release in releases or []:
            for asset in release.get("assets", []) or []:
                name = asset.get("name")
                if name:
                    assets.append({
                        "name": name,
                        "url": asset.get("browser_download_url", ""),
                        "release": release.get("tag_name", ""),
                    })
        return assets, False

    # ------------------------------------------------------------------ #
    # Matching                                                             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _find_sbom(
        root_url: Optional[str], assets: List[Dict[str, str]], has_gap: bool = False
    ) -> Dict[str, Any]:
        if root_url:
            return {
                "label": "SBOM Detection", "passing": True,
                "value": "Committed to repository", "detail": root_url,
            }
        for asset in assets:
            lower = asset["name"].lower()
            if any(hint in lower for hint in _SBOM_ASSET_HINTS):
                return {
                    "label": "SBOM Detection", "passing": True,
                    "value": f'Published with release {asset["release"]}: {asset["name"]}',
                    "detail": asset["url"],
                }
        if has_gap:
            return {"label": "SBOM Detection", "passing": False,
                     "value": None, "not_collected": True}
        return {"label": "SBOM Detection", "passing": False, "value": "No SBOM found"}

    @staticmethod
    def _find_provenance(assets: List[Dict[str, str]], has_gap: bool = False) -> Dict[str, Any]:
        for asset in assets:
            lower = asset["name"].lower()
            if any(hint in lower for hint in _PROVENANCE_ASSET_HINTS):
                return {
                    "label": "Build Provenance", "passing": True,
                    "value": f'Published with release {asset["release"]}: {asset["name"]}',
                    "detail": asset["url"],
                }
        if has_gap:
            return {"label": "Build Provenance", "passing": False,
                     "value": None, "not_collected": True}
        return {
            "label": "Build Provenance", "passing": False,
            "value": "No build provenance found",
        }

    # ------------------------------------------------------------------ #
    # Scoring                                                              #
    # ------------------------------------------------------------------ #

    def _compute_overall(self, sub_metrics: Dict[str, Any]) -> Dict[str, Any]:
        """Score only what was actually measured.

        Mirrors the convention in collaboration.py / orchestrator.yaml:
        an indicator that can't be automated is dropped from the average
        rather than counted as a failure (CASS §3.5).
        """
        scorable = {k: v for k, v in sub_metrics.items() if not v.get("not_collected")}
        score = sum(1 for v in scorable.values() if v.get("passing"))
        max_score = len(scorable)
        return {
            "score": score,
            "max_score": max_score,
            "percentage": round(score / max_score * 100, 2) if max_score else 0.0,
        }

    def _empty_result(self, repo_name: str) -> Dict[str, Any]:
        sub_metrics = {
            "sbom_detection": {"label": "SBOM Detection", "value": None, "passing": False},
            "build_provenance": {"label": "Build Provenance", "value": None, "passing": False},
            "dependency_vulnerability_posture": {
                "label": "Dependency Vulnerability Posture",
                "value": None, "passing": False, "not_collected": True,
            },
            "dependency_freshness": {
                "label": "Dependency Freshness",
                "value": None, "passing": False, "not_collected": True,
            },
        }
        return {
            "package_name": repo_name,
            "repository": "unknown",
            "timestamp": self._get_timestamp(),
            "has_sbom": False,
            "has_build_provenance": False,
            "sub_metrics": sub_metrics,
            "overall_score": {"score": 0, "max_score": 2, "percentage": 0.0},
        }
