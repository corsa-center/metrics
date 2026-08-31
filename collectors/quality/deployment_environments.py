"""
Deployment Environment Collector (CASS Report Section 4.3.5 — Accessibility)

Fills "Deployment Environment Testing" by reading the CI workflow definitions
and working out which operating-system families the project actually builds and
tests on.

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

# Cap on workflow files read, to bound the request count on repositories that
# carry dozens of them (HDF5 has ~40).
_MAX_WORKFLOW_FILES = 25

# Building on more than one OS family is what this sub-metric is asking about.
_MIN_OS_FAMILIES = 2


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
            files = await self._list_workflows(client, owner, repo)
            if not files:
                return self._empty_result(repo_name, f"{owner}/{repo}")

            contents = await asyncio.gather(
                *[self._read_workflow(client, f["url"]) for f in files[:_MAX_WORKFLOW_FILES]],
                return_exceptions=True,
            )

        families: Dict[str, set] = {name: set() for name in _RUNNER_FAMILIES}
        for text in contents:
            if isinstance(text, Exception) or not text:
                continue
            for family, pattern in _RUNNER_FAMILIES.items():
                for label in pattern.findall(text):
                    families[family].add(label.lower())

        detected = {f: sorted(labels) for f, labels in families.items() if labels}
        return {
            "package_name": repo_name,
            "repository": f"{owner}/{repo}",
            "timestamp": self._get_timestamp(),
            "workflow_count": len(files),
            "workflows_scanned": min(len(files), _MAX_WORKFLOW_FILES),
            "os_families": detected,
            "overall_score": self._calculate_score(detected),
        }

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

    def _calculate_score(self, detected: Dict[str, List[str]]) -> Dict[str, Any]:
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
        return {
            "score": 1 if passing else 0,
            "max_score": 1,
            "percentage": 100.0 if passing else 0.0,
            "sub_scores": {
                "deployment_environment_testing": {
                    "label": "Deployment Environment Testing",
                    "value": value,
                    "detail": None,
                    "passing": passing,
                }
            },
        }

    def _empty_result(self, repo_name: str, repository: str = "unknown") -> Dict[str, Any]:
        return {
            "package_name": repo_name,
            "repository": repository,
            "timestamp": self._get_timestamp(),
            "workflow_count": 0,
            "workflows_scanned": 0,
            "os_families": {},
            "overall_score": self._calculate_score({}),
        }
