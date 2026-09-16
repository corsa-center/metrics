"""
Reproducibility Collector (CASS Report Section 4.3.3)

Measures a project's ability to produce consistent, verifiable results by
checking for:
  - Containers       : Dockerfile, docker-compose, Singularity / Apptainer
  - Dependency locks : pip lock files, Poetry, Conda-lock, Cargo, Go, etc.
  - FAIR4RS metadata : CITATION.cff, codemeta.json, .zenodo.json
  - Semantic versioning: whether GitHub releases follow semver (x.y.z)

Semantic versioning is the one "Moderate" step — it requires a GitHub
releases API call rather than a simple file-existence check.
"""

import asyncio
import httpx
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from collectors.ecosystem.base import COLLECTION_GAP, GitHubCollectorBase, RetryingTransport

logger = logging.getLogger(__name__)

_SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")

# File-presence categories: label -> candidate paths
_FILE_CHECKS: Dict[str, Dict[str, List[str]]] = {
    "containers": {
        "Dockerfile": ["Dockerfile", "docker/Dockerfile", ".docker/Dockerfile"],
        "docker-compose": ["docker-compose.yml", "docker-compose.yaml"],
        "Singularity / Apptainer": [
            "Singularity",
            "singularity/Singularity",
            "Apptainer",
            "apptainer/Apptainer",
        ],
    },
    "dependency_pinning": {
        "pip lock (requirements.txt)": [
            "requirements.txt",
            "requirements/requirements.txt",
        ],
        "Poetry lock": ["poetry.lock"],
        "Pipfile.lock": ["Pipfile.lock"],
        "conda-lock": ["conda-lock.yml", "conda-lock.yaml"],
        "package-lock.json": ["package-lock.json"],
        "yarn.lock": ["yarn.lock"],
        "Cargo.lock": ["Cargo.lock"],
        "go.sum": ["go.sum"],
        "uv.lock / pdm.lock": ["uv.lock", "pdm.lock"],
    },
    "fair4rs_metadata": {
        "CITATION.cff": ["CITATION.cff"],
        "codemeta.json": ["codemeta.json"],
        "Zenodo metadata": [".zenodo.json", "zenodo.json"],
    },
    # Documentation telling someone how to rebuild the software as released.
    # Reproducibility that isn't written down can't be followed by anyone else.
    "reproducibility_docs": {
        "Install / build guide": [
            "INSTALL.md", "INSTALL", "INSTALL.txt", "BUILD.md", "BUILDING.md",
            "docs/install.md", "docs/installation.md", "doc/install.md",
            "docs/INSTALL.md", "release_docs/INSTALL", "release_docs/INSTALL.md",
        ],
        "Release / build notes": [
            "CHANGELOG.md", "CHANGELOG", "NEWS.md", "RELEASE.txt",
            "release_docs/CHANGELOG.md", "release_docs/RELEASE.txt",
            "docs/changelog.md", "HISTORY.md",
        ],
        # Dockerfile is deliberately absent: the "containers" category already
        # scores it, and counting it twice would inflate the section.
        "Environment specification": [
            "environment.yml", "environment.yaml", "spack.yaml", "spack.lock",
            ".devcontainer/devcontainer.json", ".devcontainer.json",
        ],
    },
}

# Weights used to compute the overall percentage score.
_WEIGHTS = {
    "containers": 0.20,
    "dependency_pinning": 0.30,
    "fair4rs_metadata": 0.20,
    "reproducibility_docs": 0.15,
    "semantic_versioning": 0.15,
}


class ReproducibilityCollector(GitHubCollectorBase):
    """Collects reproducibility indicators (CASS Report Section 4.3.3)."""

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        repo_name = package.get("name", "Unknown")
        repo_url = package.get("repo_url", "")

        owner_repo = self._extract_owner_repo(repo_url)
        if not owner_repo:
            logger.error(f"Could not extract owner/repo from {repo_url}")
            return self._empty_result(repo_name)

        owner, repo = owner_repo
        logger.info(f"Collecting reproducibility metrics for {owner}/{repo}")

        async with httpx.AsyncClient(timeout=30.0, transport=RetryingTransport()) as client:
            file_results, semver = await asyncio.gather(
                self._scan_files(client, owner, repo),
                self._check_semantic_versioning(client, owner, repo),
            )

        categories = {**file_results, "semantic_versioning": semver}
        overall = self._compute_overall(categories)

        return {
            "package_name": repo_name,
            "repository": f"{owner}/{repo}",
            "timestamp": self._get_timestamp(),
            "has_container": bool(categories["containers"]["found"]),
            "has_dependency_pinning": bool(categories["dependency_pinning"]["found"]),
            "has_fair4rs_metadata": bool(categories["fair4rs_metadata"]["found"]),
            "has_reproducibility_docs": bool(categories["reproducibility_docs"]["found"]),
            "uses_semantic_versioning": semver["uses_semver"],
            "categories": categories,
            "overall_score": overall,
        }

    # ------------------------------------------------------------------ #
    # File scanning                                                        #
    # ------------------------------------------------------------------ #

    async def _scan_files(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> Dict[str, Any]:
        results: Dict[str, Any] = {}

        for category, items in _FILE_CHECKS.items():
            found: List[str] = []
            missing: List[str] = []
            not_collected: List[str] = []
            details: Dict[str, Any] = {}

            async def check_item(label: str, paths: List[str]) -> Tuple[str, str, Optional[str], bool]:
                saw_gap = False
                for path in paths:
                    html_url = await self._check_file_exists(client, owner, repo, path)
                    if html_url is COLLECTION_GAP:
                        saw_gap = True
                        continue
                    if html_url:
                        return label, path, html_url, saw_gap
                return label, paths[0], None, saw_gap

            hits = await asyncio.gather(
                *[check_item(label, paths) for label, paths in items.items()]
            )

            for label, matched_path, html_url, saw_gap in hits:
                if html_url:
                    found.append(label)
                    details[label] = {
                        "exists": True,
                        "file": matched_path,
                        "url": html_url,
                    }
                    logger.debug(f"  {category}/{label}: {matched_path}")
                elif saw_gap:
                    not_collected.append(label)
                    details[label] = {"not_collected": True}
                else:
                    missing.append(label)
                    details[label] = {"exists": False}

            count_total = len(items) - len(not_collected)
            results[category] = {
                "found": found,
                "missing": missing,
                "not_collected": not_collected,
                "details": details,
                "count_found": len(found),
                "count_total": count_total,
                "percentage": round(len(found) / count_total * 100, 1) if count_total else None,
            }

        return results

    # ------------------------------------------------------------------ #
    # Semantic versioning (GitHub releases API)                           #
    # ------------------------------------------------------------------ #

    async def _check_semantic_versioning(
        self, client: httpx.AsyncClient, owner: str, repo: str, sample: int = 5
    ) -> Dict[str, Any]:
        releases = await self._github_get(
            client, f"https://api.github.com/repos/{owner}/{repo}/releases",
            params={"per_page": sample},
        )
        if releases is COLLECTION_GAP:
            return {
                "uses_semver": False, "releases_checked": 0, "semver_count": 0,
                "example_tags": [], "not_collected": True,
            }

        if not releases:
            # Confirmed no formal releases -- fall back to tags.
            return await self._check_tags(client, owner, repo, sample)

        tags = [r.get("tag_name", "") for r in releases]
        semver_tags = [t for t in tags if _SEMVER_RE.match(t)]
        uses_semver = len(semver_tags) > 0

        return {
            "uses_semver": uses_semver,
            "releases_checked": len(tags),
            "semver_count": len(semver_tags),
            "example_tags": tags[:3],
        }

    async def _check_tags(
        self, client: httpx.AsyncClient, owner: str, repo: str, sample: int
    ) -> Dict[str, Any]:
        tags_data = await self._github_get(
            client, f"https://api.github.com/repos/{owner}/{repo}/tags",
            params={"per_page": sample},
        )
        if tags_data is COLLECTION_GAP:
            return {
                "uses_semver": False, "releases_checked": 0, "semver_count": 0,
                "example_tags": [], "not_collected": True,
            }

        tags = [t.get("name", "") for t in tags_data or []]
        semver_tags = [t for t in tags if _SEMVER_RE.match(t)]

        return {
            "uses_semver": len(semver_tags) > 0,
            "releases_checked": len(tags),
            "semver_count": len(semver_tags),
            "example_tags": tags[:3],
        }

    # ------------------------------------------------------------------ #
    # Scoring                                                              #
    # ------------------------------------------------------------------ #

    def _compute_overall(self, categories: Dict[str, Any]) -> Dict[str, Any]:
        # A category is fully gapped (percentage None for a file-check
        # category, or not_collected for semantic_versioning) is dropped
        # from the blend and the remaining weights re-normalized, rather
        # than letting a gap silently count as 0% (CASS §3.5).
        weighted = 0.0
        weight_total = 0.0
        for cat, weight in _WEIGHTS.items():
            data = categories.get(cat, {})
            if cat == "semantic_versioning":
                if data.get("not_collected"):
                    continue
                pct = 100.0 if data.get("uses_semver") else 0.0
            else:
                pct = data.get("percentage")
                if pct is None:
                    continue
            weighted += pct * weight
            weight_total += weight

        if not weight_total:
            return {"score": None, "max_score": 100.0, "percentage": None, "status": "not_collected"}

        normalized = weighted / weight_total
        return {
            "score": round(normalized, 1),
            "max_score": 100.0,
            "percentage": round(normalized, 1),
            "coverage": round(weight_total, 2),
        }

    def _empty_result(self, repo_name: str) -> Dict[str, Any]:
        return {
            "package_name": repo_name,
            "repository": "unknown",
            "timestamp": self._get_timestamp(),
            "has_container": False,
            "has_dependency_pinning": False,
            "has_fair4rs_metadata": False,
            "has_reproducibility_docs": False,
            "uses_semantic_versioning": False,
            "categories": {},
            "overall_score": {"score": 0.0, "max_score": 100.0, "percentage": 0.0},
        }
