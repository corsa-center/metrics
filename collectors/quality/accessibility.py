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
import base64
import httpx
import logging
import re
from typing import Any, Dict, List, Optional

from collectors.ecosystem.base import (
    _VENDORED_DIR, COLLECTION_GAP, CONTAINER_FILE_PATTERNS, GitHubCollectorBase, RepoTree, RetryingTransport,
)

logger = logging.getLogger(__name__)

# E4S builds its Docker / Singularity images (ecpe4s/e4s-cpu and the GPU
# variants) from these Spack environments, so a package listed here is
# available as a container even without a recipe in its own repository.
_E4S_ENVIRONMENT = ("https://raw.githubusercontent.com/E4S-Project/e4s/HEAD/"
                    "environments/x86_64/gnu/cpu/spack.yaml")
_E4S_SPEC = re.compile(r"^\s*-\s*([a-z0-9][\w-]*)", re.M)
_e4s_specs: Optional[set] = None
_e4s_lock = asyncio.Lock()

# Each category maps a human-readable label to candidate file paths, matched
# case-insensitively against a RepoTree (see base.py).
_CHECKS: Dict[str, Dict[str, List[str]]] = {
    "containers": {
        "Docker": ["Dockerfile", "docker/Dockerfile", ".docker/Dockerfile"],
        "Singularity / Apptainer": [
            "Singularity",
            "singularity/Singularity",
            "Apptainer",
            "apptainer/Apptainer",
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
        "Makefile": [
            "Makefile", "makefile", "GNUmakefile", "Makefile.in", "GNUmakefile.in",
            # Autotools projects (open-mpi/ompi, pmodels/mpich, and four
            # other portfolio repos) ship Makefile.am, the automake source,
            # not a literal Makefile/GNUmakefile.
            "Makefile.am",
        ],
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


# Build and install paths beyond the labelled root files, in order of
# preference: other portable build systems, a Spack recipe or environment
# kept below the root, a build one directory down (llvm/, src/), and a root
# installation script -- which the report names alongside build systems.
_NON_BUILD_DIR = r"(?!(?:tests?|examples?|docs?|data|tutorials?|benchmarks?|demos?|sphinx)/)"
_NON_BUILD_PATH = re.compile(r"(?:^|/)(?:tests?|examples?|docs?|tutorials?|demos?|templates?)[/_-]", re.I)
_OTHER_BUILD_PATTERNS = [
    ("Meson", r"^meson\.build$"),
    ("Fortran Package Manager", r"^fpm\.toml$"),
    # A recipe in Spack's repository layout, or a Spack environment.
    ("Spack", r"(?:^|/)packages/[^/]+/package\.py$|(?:^|/)spack\.yaml$"),
    ("CMake", rf"^{_NON_BUILD_DIR}[^/.][^/]*/CMakeLists\.txt$"),
    ("Autoconf / configure", rf"^(?:{_NON_BUILD_DIR}[^/.][^/]*/)?configure(?:\.ac|\.in)?$"),
    ("Install script", r"^(?:install|build)\.sh$"),
]

# Labels also searched across the whole tree when no candidate path matches.
_TREE_PATTERNS = {
    ("containers", label): pattern
    for label, pattern in CONTAINER_FILE_PATTERNS.items() if label != "docker-compose"
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
            tree = await RepoTree.fetch(client, self.github_headers, owner, repo)
            result = self._scan(tree, repo_name, owner, repo)
            if not result["has_portable_build_system"] and tree is not COLLECTION_GAP:
                other = self._other_build(tree)
                if other:
                    result["has_portable_build_system"] = True
                    result["other_build"] = other
            if not result["has_container"] and tree is not COLLECTION_GAP:
                image = await self._e4s_image(client, owner, repo)
                if image:
                    result["has_container"] = True
                    result["container_image"] = image
            if not result["has_portable_build_system"] and tree is not COLLECTION_GAP:
                package = await self._python_package(client, owner, repo, result)
                if package:
                    result["has_portable_build_system"] = True
                    result["python_package"] = package
            return result

    async def _e4s_image(self, client: httpx.AsyncClient, owner: str, repo: str) -> Optional[str]:
        """"E4S container image (Spack package <name>)" when the project's
        Spack recipe is in the E4S image environment, else None."""
        global _e4s_specs
        async with _e4s_lock:
            if _e4s_specs is None:
                try:
                    resp = await client.get(_E4S_ENVIRONMENT)
                except httpx.HTTPError:
                    return None
                if resp.status_code != 200:
                    return None
                _e4s_specs = set(_E4S_SPEC.findall(resp.text.split("specs:", 1)[-1]))
        # The same recipe lookup the collaboration collector uses: the repo's
        # own name, then recipes whose URLs are this repository.
        from collectors.ecosystem.collaboration import CollaborationCollector
        names = [repo.lower()] + await CollaborationCollector()._main_spack_recipe(client, owner, repo)
        for name in names:
            if name in _e4s_specs:
                return f"E4S container image (Spack package {name})"
        return None

    @staticmethod
    def _other_build(tree) -> Optional[str]:
        """A portable build or install path the root-level labels miss,
        described with its file, or None. Kept out of the labelled
        categories so their percentages don't change for every project."""
        for label, pattern in _OTHER_BUILD_PATTERNS:
            hits = [p for p in tree.find(pattern)
                    if not _VENDORED_DIR.search(p) and not _NON_BUILD_PATH.search(p)]
            if hits:
                return f"{label} ({min(hits, key=lambda p: (p.count('/'), p))})"
        return None

    async def _python_package(
        self, client: httpx.AsyncClient, owner: str, repo: str, result: Dict[str, Any]
    ) -> Optional[str]:
        """The file that makes the project pip-installable, or None.

        A pip-installable package is the portable, cross-platform install for
        a Python project, which otherwise has none of the listed build
        systems. A pyproject.toml counts only if it declares a build or
        project table; many hold nothing but tool settings (ruff, black)
        for a C++ codebase.
        """
        details = result["categories"].get("python_packaging", {}).get("details", {})
        if details.get("setup.py", {}).get("exists"):
            return details["setup.py"]["file"]
        path = details.get("pyproject.toml", {}).get("file")
        if not path:
            return None
        data = await self._github_get(
            client, f"https://api.github.com/repos/{owner}/{repo}/contents/{path}")
        if not isinstance(data, dict):
            return None
        text = base64.b64decode(data.get("content", "")).decode("utf-8", "replace")
        return path if re.search(r"^\[(?:build-system|project)\]", text, re.M) else None

    def _scan(
        self,
        tree,
        repo_name: str,
        owner: str,
        repo: str,
    ) -> Dict[str, Any]:
        """Resolved against a RepoTree (or COLLECTION_GAP) rather than probed
        one literal path at a time -- see METRIC_BLIND_SPOTS.md class F1/F2.
        """
        category_results: Dict[str, Any] = {}
        all_found: List[str] = []
        all_missing: List[str] = []
        all_not_collected: List[str] = []

        for category, items in _CHECKS.items():
            found: List[str] = []
            missing: List[str] = []
            not_collected: List[str] = []
            details: Dict[str, Any] = {}

            for label, paths in items.items():
                if tree is COLLECTION_GAP:
                    not_collected.append(label)
                    details[label] = {"not_collected": True}
                    continue
                matched_path = tree.match(paths)
                url = tree.match_url(paths) if matched_path else None
                pattern = _TREE_PATTERNS.get((category, label))
                if not matched_path and pattern:
                    matched_path = tree.find_owned(pattern)
                    url = tree.url_for(matched_path) if matched_path else None
                if matched_path:
                    found.append(label)
                    details[label] = {
                        "exists": True,
                        "file": matched_path,
                        "url": url,
                    }
                    logger.debug(f"  {category}/{label}: {matched_path}")
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
