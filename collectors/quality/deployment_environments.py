"""
Deployment Environment Collector (CASS Report Section 4.3.5 — Accessibility)

Fills the three CI- and documentation-derived sub-metrics of section 4.3.5 by
reading the workflow definitions once:

  - Deployment Environment Testing    : which OS families CI builds on
  - Architecture Compatibility Analysis : which CPU architectures CI covers
  - Platform Documentation Evaluation : whether the docs say what is supported

Portable Build System Detection and Container Availability are collected by
accessibility.py.

Runner labels are matched anywhere in the workflow text rather than only after
`runs-on:`, because most real workflows write `runs-on: ${{ matrix.os }}` and
list the concrete runners in the matrix block further down.

Labels are folded to OS *families* — a project testing ubuntu-22.04 and
ubuntu-24.04 covers one environment, not two.
"""

import asyncio
import base64
import logging
import re
from typing import Any, Dict, List, Optional

import httpx

from collectors.sustainability.base import GitHubCollectorBase

logger = logging.getLogger(__name__)

_WORKFLOWS_DIR = ".github/workflows"

# Runner label prefixes that GitHub-hosted and common self-hosted runners use,
# mapped to the OS family they represent.
# The version suffix matters: matching any word after the family name sweeps up
# job names like "macos-clang" and "linux-oneapi", which are toolchain labels
# rather than runners and make the reported detail wrong.
_RUNNER_SUFFIX = r"(?:latest|\d+(?:\.\d+)?)"
_RUNNER_FAMILIES = {
    "Linux": re.compile(rf"\b(?:ubuntu|debian|fedora|rhel|centos)-{_RUNNER_SUFFIX}\b", re.I),
    "Windows": re.compile(rf"\bwindows-{_RUNNER_SUFFIX}\b", re.I),
    "macOS": re.compile(rf"\bmacos-{_RUNNER_SUFFIX}\b", re.I),
}

# Explicit CPU architecture tokens. x86-64 is not listed: it is the implicit
# default for every standard runner, so naming it proves nothing. What this
# detects is a project that went out of its way to test something else.
_ARCH_PATTERNS = {
    # The `-arm` runner suffix follows a version, not letters
    # (`ubuntu-24.04-arm`), so the prefix must not be constrained to [a-z].
    "ARM64": re.compile(r"\barm64\b|\baarch64\b|-arm\b|\barm-", re.I),
    "POWER": re.compile(r"\b(?:ppc64le|ppc64|power[89])\b", re.I),
    "RISC-V": re.compile(r"\briscv(?:64)?\b", re.I),
    "s390x": re.compile(r"\bs390x\b", re.I),
}

# Platform names a project might document support for.
_PLATFORM_DOC_TERMS = {
    "Linux": re.compile(r"\b(?:linux|ubuntu|debian|centos|rhel|red hat|fedora|suse)\b", re.I),
    "Windows": re.compile(r"\b(?:windows|win32|win64|msvc|mingw)\b", re.I),
    "macOS": re.compile(r"\b(?:macos|mac os|osx|darwin|apple)\b", re.I),
    "HPC systems": re.compile(r"\b(?:cray|frontier|summit|perlmutter|aurora|slurm|hpc cluster)\b", re.I),
}

# Cap on workflow files read, to bound the request count on repositories that
# carry dozens of them (HDF5 has ~40).
_MAX_WORKFLOW_FILES = 25

# Building on more than one OS family is what this sub-metric is asking about.
_MIN_OS_FAMILIES = 2
# Any explicitly-tested non-x86 architecture means the project is portable
# beyond the default; x86-64 plus one other is the bar.
_MIN_EXTRA_ARCHITECTURES = 1
# Naming at least two supported platforms counts as documenting portability.
_MIN_DOCUMENTED_PLATFORMS = 2


class DeploymentEnvironmentCollector(GitHubCollectorBase):
    """Detects the OS families a project's CI exercises (Section 4.3.5)."""

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        repo_name = package.get("name", "Unknown")
        owner_repo = self._extract_owner_repo(package.get("repo_url", ""))
        if not owner_repo:
            logger.error(f"Could not extract owner/repo from {package.get('repo_url')}")
            return self._empty_result(repo_name)

        owner, repo = owner_repo
        logger.info(f"Collecting deployment environment metrics for {repo_name}")

        async with httpx.AsyncClient(timeout=30.0) as client:
            files, doc_text = await asyncio.gather(
                self._list_workflows(client, owner, repo),
                self._read_platform_docs(client, owner, repo),
                return_exceptions=True,
            )
            if isinstance(files, Exception):
                logger.warning(f"Workflow listing failed: {files}")
                files = []
            if isinstance(doc_text, Exception):
                logger.warning(f"Platform doc read failed: {doc_text}")
                doc_text = ""

            contents = await asyncio.gather(
                *[self._read_workflow(client, f["url"]) for f in files[:_MAX_WORKFLOW_FILES]],
                return_exceptions=True,
            ) if files else []

        families: Dict[str, set] = {name: set() for name in _RUNNER_FAMILIES}
        architectures: set = set()
        for text in contents:
            if isinstance(text, Exception) or not text:
                continue
            for family, pattern in _RUNNER_FAMILIES.items():
                for label in pattern.findall(text):
                    families[family].add(label.lower())
            for arch, pattern in _ARCH_PATTERNS.items():
                if pattern.search(text):
                    architectures.add(arch)

        detected = {f: sorted(labels) for f, labels in families.items() if labels}
        documented = sorted(
            name for name, pattern in _PLATFORM_DOC_TERMS.items()
            if doc_text and pattern.search(doc_text)
        )
        return {
            "package_name": repo_name,
            "repository": f"{owner}/{repo}",
            "timestamp": self._get_timestamp(),
            "workflow_count": len(files),
            "workflows_scanned": min(len(files), _MAX_WORKFLOW_FILES),
            "os_families": detected,
            "architectures": sorted(architectures),
            "documented_platforms": documented,
            "overall_score": self._calculate_score(detected, sorted(architectures), documented),
        }

    async def _read_platform_docs(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> str:
        """README text, used to see which platforms the project claims to support."""
        try:
            resp = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/readme",
                headers=self.github_headers,
            )
            if resp.status_code != 200:
                return ""
            import base64
            return base64.b64decode(resp.json().get("content", "")).decode("utf-8", "replace")
        except Exception as e:
            logger.debug(f"Could not read README: {e}")
            return ""

    async def _list_workflows(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> List[Dict[str, str]]:
        """Workflow definition files in .github/workflows."""
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{_WORKFLOWS_DIR}"
        try:
            resp = await client.get(url, headers=self.github_headers)
            if resp.status_code != 200:
                return []
            entries = resp.json()
            if not isinstance(entries, list):
                return []
            return [
                {"name": e["name"], "url": e["download_url"]}
                for e in entries
                if e.get("name", "").endswith((".yml", ".yaml")) and e.get("download_url")
            ]
        except Exception as e:
            logger.debug(f"Could not list workflows: {e}")
            return []

    async def _read_workflow(self, client: httpx.AsyncClient, url: str) -> Optional[str]:
        """Fetch a workflow file's raw text."""
        try:
            resp = await client.get(url)
            return resp.text if resp.status_code == 200 else None
        except Exception as e:
            logger.debug(f"Could not read workflow {url}: {e}")
            return None

    def _calculate_score(
        self,
        detected: Dict[str, List[str]],
        architectures: Optional[List[str]] = None,
        documented: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Summarise coverage by OS family.

        Individual runner labels stay in `os_families` for anyone consuming the
        JSON, but they are not rendered: "ubuntu-24.04, ubuntu-latest,
        windows-11, windows-2022, windows-latest, macos-15, macos-latest" is a
        wall of text that says nothing the family list doesn't already say.
        """
        names = sorted(detected)
        passing = len(names) >= _MIN_OS_FAMILIES
        if names:
            value = f"{len(names)} environment{'s' if len(names) != 1 else ''}: " + ", ".join(names)
        else:
            value = "No CI runner environments detected"
        architectures = architectures or []
        documented = documented or []

        arch_ok = len(architectures) >= _MIN_EXTRA_ARCHITECTURES
        arch_value = (
            "x86-64 plus " + ", ".join(architectures) if architectures
            else "x86-64 only"
        ) if detected else "No CI architectures detected"

        docs_ok = len(documented) >= _MIN_DOCUMENTED_PLATFORMS
        docs_value = (
            f"{len(documented)} platform{'s' if len(documented) != 1 else ''} named: "
            + ", ".join(documented)
        ) if documented else "No supported platforms named in the README"

        sub = {
            "deployment_environment_testing": {
                "label": "Deployment Environment Testing",
                "value": value,
                "detail": None,
                "passing": passing,
            },
            "architecture_compatibility": {
                "label": "Architecture Compatibility Analysis",
                "value": arch_value,
                "detail": None,
                "passing": arch_ok,
            },
            "platform_documentation": {
                "label": "Platform Documentation Evaluation",
                "value": docs_value,
                "detail": None,
                "passing": docs_ok,
            },
        }
        score = sum(1 for v in sub.values() if v["passing"])
        return {
            "score": score,
            "max_score": len(sub),
            "percentage": round(score / len(sub) * 100, 2),
            "sub_scores": sub,
        }

    def _empty_result(self, repo_name: str, repository: str = "unknown") -> Dict[str, Any]:
        return {
            "package_name": repo_name,
            "repository": repository,
            "timestamp": self._get_timestamp(),
            "workflow_count": 0,
            "workflows_scanned": 0,
            "os_families": {},
            "architectures": [],
            "documented_platforms": [],
            "overall_score": self._calculate_score({}, [], []),
        }
