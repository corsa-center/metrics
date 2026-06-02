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

from collectors.sustainability.base import GitHubCollectorBase

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
        "Makefile": ["Makefile", "makefile", "GNUmakefile"],
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

        async with httpx.AsyncClient(timeout=30.0) as client:
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

        for category, items in _CHECKS.items():
            found: List[str] = []
            missing: List[str] = []
            details: Dict[str, Any] = {}

            # Check each item in the category concurrently.
            async def check_item(label: str, paths: List[str]) -> tuple:
                for path in paths:
                    if await self._check_file_exists(client, owner, repo, path):
                        return label, path, True
                return label, paths[0], False

            results = await asyncio.gather(
                *[check_item(label, paths) for label, paths in items.items()]
            )

            for label, matched_path, exists in results:
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

            all_found.extend(found)
            all_missing.extend(missing)
            total = len(items)
            category_results[category] = {
                "found": found,
                "missing": missing,
                "details": details,
                "count_found": len(found),
                "count_total": total,
                "percentage": round(len(found) / total * 100, 1) if total else 0.0,
            }

        total_checks = len(all_found) + len(all_missing)
        overall_pct = round(len(all_found) / total_checks * 100, 1) if total_checks else 0.0

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
                "score": len(all_found),
                "max_score": total_checks,
                "percentage": overall_pct,
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
