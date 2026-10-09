"""
Static Analysis / CodeQL Collector (CASS Report Section 4.3.1 — Enhanced Security Analysis)

Detects whether a repository runs code scanning: GitHub CodeQL, or GitLab
SAST (enabled by including GitLab's SAST template in .gitlab-ci.yml). The
result key stays `has_codeql` for compatibility with stored output; `scanner`
names what was actually found.

On GitHub this checks for a CodeQL workflow file. GitHub's code-scanning alerts API
(/repos/{owner}/{repo}/code-scanning/alerts) requires authentication even
for public repos (returns 401 unauthenticated), so this uses the same
workflow-presence proxy pattern as
collectors/ecosystem/openssf_badge.py rather than fetching alert counts.
"""

import asyncio
import httpx
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from forge.base import COLLECTION_GAP, RetryingTransport
from forge.interface import Forge

logger = logging.getLogger(__name__)

# Code-scanning markers searched for in CI file contents, in report order.
_SCANNER_MARKERS: List[Tuple[str, "re.Pattern[str]"]] = [
    ("CodeQL", re.compile(r"codeql-action|\bcodeql\s+database\b", re.I)),
    ("GitLab SAST", re.compile(
        r"(?:Security|Jobs)/SAST(?:-IaC)?(?:\.latest)?\.gitlab-ci\.yml|components/sast/", re.I
    )),
]

# What a negative result means on each platform, for the rendered "not found" line.
_SCANNERS_CHECKED = {"github": "CodeQL", "gitlab": "CodeQL or GitLab SAST"}
# Bounds worst-case API calls per repo when falling back to a content scan.
_MAX_WORKFLOWS_TO_SCAN = 25


class StaticAnalysisCollector:
    """Detects CodeQL / static analysis security scanning (Section 4.3.1)."""

    def __init__(self, forge: Forge):
        self.forge = forge

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        repo_name = package.get("name", "Unknown")
        repo_url = package.get("repo_url", "")

        ref = self.forge.extract_ref(repo_url)
        if not ref:
            logger.error(f"Could not extract a repo reference from {repo_url}")
            return self._empty_result(repo_name)

        logger.info(f"Checking CodeQL / static analysis for {ref}")

        checked = _SCANNERS_CHECKED.get(self.forge.platform, "CodeQL")
        async with httpx.AsyncClient(timeout=30.0, transport=RetryingTransport()) as client:
            saw_gap = False
            for path in self.forge.platform_paths("security_scan_workflows"):
                html_url = await self.forge.file_exists(client, ref, path)
                if html_url is COLLECTION_GAP:
                    saw_gap = True
                    continue
                if html_url:
                    return {
                        "package_name": repo_name,
                        "repository": ref,
                        "timestamp": self.forge.get_timestamp(),
                        "has_codeql": True,
                        "scanner": "CodeQL",
                        "scanners_checked": checked,
                        "workflow_file": path,
                        "workflow_url": html_url,
                    }

            # None of the common filenames matched — some projects bundle CodeQL
            # into a differently-named workflow (e.g. ADIOS2's `everything.yml`).
            # Fall back to scanning workflow file contents for a codeql-action
            # reference, since filename guessing alone produces false negatives.
            # On GitLab this content scan is the only check.
            found, scan_gap = await self._scan_workflows_for_codeql(client, ref)
            if found:
                return {
                    "package_name": repo_name,
                    "repository": ref,
                    "timestamp": self.forge.get_timestamp(),
                    "has_codeql": True,
                    "scanner": found["scanner"],
                    "scanners_checked": checked,
                    "workflow_file": found["file"],
                    "workflow_url": found["url"],
                }
            saw_gap = saw_gap or scan_gap

        result = {
            "package_name": repo_name,
            "repository": ref,
            "timestamp": self.forge.get_timestamp(),
            "has_codeql": False,
            "scanners_checked": checked,
            "workflow_file": None,
            "workflow_url": None,
        }
        # A False here built on a gap isn't a confirmed "no CodeQL" -- the
        # gap could be hiding the workflow file that would have matched.
        if saw_gap:
            result["not_collected"] = True
        return result

    async def _scan_workflows_for_codeql(
        self, client: httpx.AsyncClient, ref: str
    ) -> tuple:
        """Scan CI file contents for a code-scanning marker (_SCANNER_MARKERS).

        Returns (match_or_None, saw_gap).
        """
        entries = await self.forge.ci_config_files(client, ref)
        if entries is COLLECTION_GAP:
            return None, True

        yaml_files = list(entries)[:_MAX_WORKFLOWS_TO_SCAN]

        async def check_file(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            text = await self.forge.file_content(client, ref, entry["path"])
            if text is COLLECTION_GAP:
                return COLLECTION_GAP
            if text:
                for scanner, pattern in _SCANNER_MARKERS:
                    if pattern.search(text):
                        return {"file": entry["path"], "url": entry.get("html_url", ""),
                                "scanner": scanner}
            return None

        results = await asyncio.gather(*[check_file(e) for e in yaml_files])
        saw_gap = any(r is COLLECTION_GAP for r in results)
        for result in results:
            if result and result is not COLLECTION_GAP:
                return result, saw_gap
        return None, saw_gap

    def _empty_result(self, repo_name: str) -> Dict[str, Any]:
        return {
            "package_name": repo_name,
            "repository": "unknown",
            "timestamp": self.forge.get_timestamp(),
            "has_codeql": False,
            "workflow_file": None,
            "workflow_url": None,
        }
