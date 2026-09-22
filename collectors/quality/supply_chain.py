"""
Software Supply Chain Integrity Collector (CASS Report Section 4.3.8)

Checks the transparency and verifiability of a project's build and
distribution pipeline:
  - SBOM Detection                : an SPDX/CycloneDX bill of materials,
                                     committed to the repository root or
                                     published as a release asset
  - Build Provenance              : a SLSA / in-toto attestation published
                                     alongside a release
  - Dependency Vulnerability Posture : known vulnerabilities in the
                                     project's own pinned Python
                                     dependencies, via OSV.dev's free batch
                                     query API -- no Dependabot alert access
                                     needed on the target repo, unlike
                                     GitHub's own vulnerability-alerts API.
                                     Coverage is real but narrow: only
                                     requirements.txt-pinned PyPI packages
                                     with an exact == version are checked.
                                     Other ecosystems (Cargo.lock,
                                     poetry.lock, go.sum, ...) aren't parsed
                                     yet -- requirements.txt alone covers
                                     the most of this portfolio (25 of 71
                                     tracked repos have one; 9 pin it at the
                                     repository root).

Dependency Freshness (libyears) is not collected here: it needs a
machine-readable dependency manifest with enough history to compute a lag,
which is a separate, larger undertaking from checking pinned versions
against known vulnerabilities. Per CASS §3.5, it's reported as
not-yet-collected rather than scored zero.

Badge and Scorecard level are not fetched here either — they're already
collected by openssf_badge.py / openssf_scorecard.py (Section 4.2) and are
surfaced alongside this section by the dashboard transform rather than
re-fetched.
"""

import asyncio
import base64
import httpx
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from collectors.ecosystem.base import COLLECTION_GAP, GitHubCollectorBase, RepoTree, RetryingTransport

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

# Root-level only, matching reproducibility.py's own "pip lock" candidates.
# requirements.txt shows up at 25 other, nested paths across the portfolio
# (docs/requirements.txt, .github/workflows/.../requirements.txt,
# tests/requirements.txt) that are Sphinx/CI/test tooling pins, not the
# project's own dependency surface -- deliberately not matched here, unlike
# the case-insensitive-but-otherwise-permissive RepoTree.match() used
# elsewhere, since flagging a stale Sphinx theme pin as a "supply chain"
# finding would be noise, not signal.
_REQUIREMENTS_TXT_PATHS = ["requirements.txt", "requirements/requirements.txt"]

# An exactly-pinned PyPI requirement line: name[extras]==version, with an
# optional environment marker or comment already stripped by the caller.
# OSV.dev's query API takes a single version, not a range, so anything
# short of == (>=, ~=, a bare name) names a real dependency this can't
# check -- silently skipped rather than guessed at.
_PYPI_PIN_RE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*==\s*([A-Za-z0-9][A-Za-z0-9.+!_-]*)$"
)

_OSV_BATCH_URL = "https://api.osv.dev/v1/querybatch"


def _parse_pinned_pypi_deps(text: str) -> List[Tuple[str, str]]:
    """Exactly-pinned (name, version) pairs from a requirements.txt-shaped
    file. Lines using a range (>=, ~=, <), a bare name, a VCS/URL
    requirement, or a pip option (-e, -r, --index-url) are skipped --
    real dependencies, just not ones a single-version query can check.
    """
    deps = []
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].split(";", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        match = _PYPI_PIN_RE.match(line)
        if match:
            deps.append((match.group(1), match.group(2)))
    return deps


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
            (release_assets, assets_gap), tree = await asyncio.gather(
                self._fetch_release_assets(client, owner, repo),
                RepoTree.fetch(client, self.github_headers, owner, repo),
            )
            root_sbom, root_gap = self._check_root_sbom(tree)
            dep_vulns = await self._check_dependency_vulnerabilities(client, owner, repo, tree)

        sbom = self._find_sbom(root_sbom, release_assets, root_gap or assets_gap)
        provenance = self._find_provenance(release_assets, assets_gap)

        sub_metrics = {
            "sbom_detection": sbom,
            "build_provenance": provenance,
            "dependency_vulnerability_posture": dep_vulns,
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

    def _check_root_sbom(self, tree) -> tuple:
        """Returns (html_url_or_None, saw_gap). Resolved against a RepoTree
        (case-insensitive, single fetch) rather than probed one literal path
        at a time -- see METRIC_BLIND_SPOTS.md class F1.
        """
        if tree is COLLECTION_GAP:
            return None, True
        return tree.match_url(_SBOM_ROOT_FILES), False

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

    async def _check_dependency_vulnerabilities(
        self, client: httpx.AsyncClient, owner: str, repo: str, tree
    ) -> Dict[str, Any]:
        """Dependency Vulnerability Posture: known vulnerabilities in the
        project's exactly-pinned PyPI dependencies, via OSV.dev's free,
        unauthenticated batch query API -- no Dependabot alert access needed
        on the target repo, unlike GitHub's own vulnerability-alerts API.
        """
        if tree is COLLECTION_GAP:
            return {"label": "Dependency Vulnerability Posture", "value": None,
                    "passing": False, "not_collected": True}

        path = tree.match(_REQUIREMENTS_TXT_PATHS)
        if not path:
            return {
                "label": "Dependency Vulnerability Posture", "passing": True,
                "value": "No requirements.txt at the repository root",
            }

        data = await self._github_get(
            client, f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
        )
        if data is COLLECTION_GAP:
            return {"label": "Dependency Vulnerability Posture", "value": None,
                    "passing": False, "not_collected": True}
        if data is None:
            return {
                "label": "Dependency Vulnerability Posture", "passing": True,
                "value": "No requirements.txt at the repository root",
            }

        text = base64.b64decode(data.get("content", "")).decode("utf-8", "replace")
        deps = _parse_pinned_pypi_deps(text)
        if not deps:
            return {
                "label": "Dependency Vulnerability Posture", "passing": True,
                "value": f"{path} has no exactly-pinned dependencies to check",
                "detail": path,
            }

        vulnerable = await self._query_osv_batch(client, deps)
        if vulnerable is COLLECTION_GAP:
            return {"label": "Dependency Vulnerability Posture", "value": None,
                    "passing": False, "not_collected": True}

        return {
            "label": "Dependency Vulnerability Posture",
            "passing": not vulnerable,
            "value": f"{len(vulnerable)} of {len(deps)} pinned dependencies "
                     f"have a known vulnerability",
            "detail": sorted(f"{name}=={version}" for name, version in vulnerable) or None,
        }

    async def _query_osv_batch(
        self, client: httpx.AsyncClient, deps: List[Tuple[str, str]]
    ):
        """(name, version) pairs with at least one known OSV vulnerability,
        or COLLECTION_GAP if the query itself failed. A dep absent from the
        result had a confirmed-clean scan, same as every other pair not
        returned. Severity isn't fetched -- OSV.dev's batch endpoint returns
        only vulnerability IDs, and a second round of per-ID lookups for
        detail isn't worth it for a boolean pass/fail sub-metric.
        """
        queries = [{"package": {"name": name, "ecosystem": "PyPI"}, "version": version}
                   for name, version in deps]
        try:
            resp = await client.post(_OSV_BATCH_URL, json={"queries": queries})
        except Exception as e:
            logger.warning(f"COLLECTION-GAP url={_OSV_BATCH_URL} status=exception reason={e!r}")
            return COLLECTION_GAP
        if resp.status_code != 200:
            logger.warning(f"COLLECTION-GAP url={_OSV_BATCH_URL} status={resp.status_code}")
            return COLLECTION_GAP

        results = resp.json().get("results", [])
        return [deps[i] for i, r in enumerate(results) if r.get("vulns")]

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
