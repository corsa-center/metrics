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

from collectors.sustainability.base import GitHubCollectorBase

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
}

# Weights used to compute the overall percentage score.
_WEIGHTS = {
    "containers": 0.25,
    "dependency_pinning": 0.35,
    "fair4rs_metadata": 0.25,
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

        async with httpx.AsyncClient(timeout=30.0) as client:
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
            details: Dict[str, Any] = {}

            async def check_item(label: str, paths: List[str]) -> Tuple[str, str, bool]:
                for path in paths:
                    if await self._check_file_exists(client, owner, repo, path):
                        return label, path, True
                return label, paths[0], False

            hits = await asyncio.gather(
                *[check_item(label, paths) for label, paths in items.items()]
            )

            for label, matched_path, exists in hits:
                if exists:
                    found.append(label)
                    details[label] = {
                        "exists": True,
                        "file": matched_path,
                        "url": f"https://github.com/{owner}/{repo}/blob/main/{matched_path}",
                    }
                    logger.debug(f"  {category}/{label}: {matched_path}")
                else:
                    missing.append(label)
                    details[label] = {"exists": False}

            total = len(items)
            results[category] = {
                "found": found,
                "missing": missing,
                "details": details,
                "count_found": len(found),
                "count_total": total,
                "percentage": round(len(found) / total * 100, 1) if total else 0.0,
            }

        return results

    # ------------------------------------------------------------------ #
    # Semantic versioning (GitHub releases API)                           #
    # ------------------------------------------------------------------ #

    async def _check_semantic_versioning(
        self, client: httpx.AsyncClient, owner: str, repo: str, sample: int = 5
    ) -> Dict[str, Any]:
        url = f"https://api.github.com/repos/{owner}/{repo}/releases"
        try:
            resp = await client.get(
                url,
                headers=self.github_headers,
                params={"per_page": sample},
            )
            resp.raise_for_status()
            releases = resp.json()
        except Exception as e:
            logger.warning(f"Could not fetch releases for {owner}/{repo}: {e}")
            return {"uses_semver": False, "releases_checked": 0, "semver_count": 0, "example_tags": []}

        if not releases:
            # Fall back to tags if no formal releases exist
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
        url = f"https://api.github.com/repos/{owner}/{repo}/tags"
        try:
            resp = await client.get(
                url,
                headers=self.github_headers,
                params={"per_page": sample},
            )
            resp.raise_for_status()
            tags_data = resp.json()
        except Exception as e:
            logger.warning(f"Could not fetch tags for {owner}/{repo}: {e}")
            return {"uses_semver": False, "releases_checked": 0, "semver_count": 0, "example_tags": []}

        tags = [t.get("name", "") for t in tags_data]
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
        weighted = 0.0
        for cat, weight in _WEIGHTS.items():
            if cat == "semantic_versioning":
                pct = 100.0 if categories[cat].get("uses_semver") else 0.0
            else:
                pct = categories[cat].get("percentage", 0.0)
            weighted += pct * weight

        return {
            "score": round(weighted, 1),
            "max_score": 100.0,
            "percentage": round(weighted, 1),
        }

    def _empty_result(self, repo_name: str) -> Dict[str, Any]:
        return {
            "package_name": repo_name,
            "repository": "unknown",
            "timestamp": self._get_timestamp(),
            "has_container": False,
            "has_dependency_pinning": False,
            "has_fair4rs_metadata": False,
            "uses_semantic_versioning": False,
            "categories": {},
            "overall_score": {"score": 0.0, "max_score": 100.0, "percentage": 0.0},
        }
