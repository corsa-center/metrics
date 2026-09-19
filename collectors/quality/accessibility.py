"""
Accessibility / Portability Collector (CASS Report Section 4.3.5)

Detects the presence of portable build systems and container configurations
that allow software to run across diverse computing environments.

Checks (per the report):
  - Container images     : Dockerfile, Singularity / Apptainer definition files
  - Portable build tools : CMakeLists.txt, Spack recipe (package.py), Conda
                           recipe (meta.yaml / environment.yml), Makefile,
                           Autoconf (configure.ac / configure.in)
  - Python packaging     : pyproject.toml, setup.py, setup.cfg
  - Documentation        : INSTALL, INSTALL.md
"""

import asyncio
import httpx
import logging
from typing import Any, Dict, List

from collectors.ecosystem.base import COLLECTION_GAP, GitHubCollectorBase, RetryingTransport

logger = logging.getLogger(__name__)

# Each category maps a human-readable label to candidate file paths.
_CHECKS: Dict[str, Dict[str, List[str]]] = {
    "containers": {
        "Docker": ["Dockerfile", "docker/Dockerfile", ".docker/Dockerfile"],
        "Singularity / Apptainer": [
            "Singularity",
            "singularity/Singularity",
            "Apptainer",
            "apptainer/Apptainer",
            "*.def",
        ],
    },
    "build_systems": {
        "CMake": ["CMakeLists.txt"],
        "Spack": ["package.py", "spack/package.py"],
        "Conda": [
            "meta.yaml",
            "conda/meta.yaml",
            "recipe/meta.yaml",
            "environment.yml",
            "environment.yaml",
        ],
        "Autoconf": ["configure.ac", "configure.in"],
        "Makefile": ["Makefile", "makefile", "GNUmakefile", "Makefile.in", "GNUmakefile.in"],
    },
    "python_packaging": {
        "pyproject.toml": ["pyproject.toml"],
        "setup.py": ["setup.py"],
        "setup.cfg": ["setup.cfg"],
    },
    "install_docs": {
        "INSTALL": ["INSTALL", "INSTALL.md", "INSTALL.rst", "INSTALL.txt"],
    },
}


class AccessibilityCollector(GitHubCollectorBase):
    """Detects portable build systems and container configs (Section 4.3.5)."""

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        repo_name = package.get("name", "Unknown")
        repo_url = package.get("repo_url", "")

        owner_repo = self._extract_owner_repo(repo_url)
        if not owner_repo:
            logger.error(f"Could not extract owner/repo from {repo_url}")
            return self._empty_result(repo_name)

        owner, repo = owner_repo
        logger.info(f"Checking accessibility / portability for {owner}/{repo}")

        async with httpx.AsyncClient(timeout=30.0, transport=RetryingTransport()) as client:
            return await self._scan(client, repo_name, owner, repo)

    async def _scan(
        self,
        client: httpx.AsyncClient,
        repo_name: str,
        owner: str,
        repo: str,
    ) -> Dict[str, Any]:
        category_results: Dict[str, Any] = {}
        all_found: List[str] = []
        all_missing: List[str] = []
        all_not_collected: List[str] = []

        for category, items in _CHECKS.items():
            found: List[str] = []
            missing: List[str] = []
            not_collected: List[str] = []
            details: Dict[str, Any] = {}

            # Check each item in the category concurrently.
            async def check_item(label: str, paths: List[str]) -> tuple:
                saw_gap = False
                for path in paths:
                    html_url = await self._check_file_exists(client, owner, repo, path)
                    if html_url is COLLECTION_GAP:
                        saw_gap = True
                        continue
                    if html_url:
                        return label, path, html_url, saw_gap
                return label, paths[0], None, saw_gap

            results = await asyncio.gather(
                *[check_item(label, paths) for label, paths in items.items()]
            )

            for label, matched_path, html_url, saw_gap in results:
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

            all_found.extend(found)
            all_missing.extend(missing)
            all_not_collected.extend(not_collected)
            count_total = len(items) - len(not_collected)
            category_results[category] = {
                "found": found,
                "missing": missing,
                "not_collected": not_collected,
                "details": details,
                "count_found": len(found),
                "count_total": count_total,
                "percentage": round(len(found) / count_total * 100, 1) if count_total else None,
            }

        total_checks = len(all_found) + len(all_missing)
        overall_pct = round(len(all_found) / total_checks * 100, 1) if total_checks else None

        has_container = bool(category_results["containers"]["found"])
        has_portable_build = bool(category_results["build_systems"]["found"])

        return {
            "package_name": repo_name,
            "repository": f"{owner}/{repo}",
            "timestamp": self._get_timestamp(),
            "has_container": has_container,
            "has_portable_build_system": has_portable_build,
            "categories": category_results,
            "overall_score": {
                "score": len(all_found) if total_checks else None,
                "max_score": total_checks,
                "percentage": overall_pct,
                **({"status": "not_collected"} if not total_checks else {}),
            },
        }

    def _empty_result(self, repo_name: str) -> Dict[str, Any]:
        return {
            "package_name": repo_name,
            "repository": "unknown",
            "timestamp": self._get_timestamp(),
            "has_container": False,
            "has_portable_build_system": False,
            "categories": {},
            "overall_score": {"score": 0, "max_score": 0, "percentage": 0.0},
        }
