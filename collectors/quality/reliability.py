"""
Reliability Collector (CASS Report Section 4.3.1 — Reliability and Robustness)

Fills the three sub-metrics StaticAnalysisCollector and TestCoverageCollector do
not cover:

  - Advanced Static Analysis    : defect-finding tools beyond CodeQL
  - CERT Guidelines Compliance  : secure-coding practice indicators
  - Reliability Trend Analysis  : defect reports over two comparable windows

Enhanced Security Analysis (CodeQL) and Test Coverage Excellence stay with their
existing collectors.

Two honesty notes. "CERT Guidelines Compliance" is reported as *practice
indicators* — hardening flags, sanitizers, an explicit CERT/MISRA reference —
not as conformance, which needs an audit against the guideline set. And the
trend needs the project to label defects: HDF5 labels by component and has no
bug label at all, so the row says the trend cannot be measured rather than
reporting a meaningless 0 vs 0.
"""

import asyncio
import base64
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx

from collectors.rate_limit import search_get
from collectors.sustainability.base import GitHubCollectorBase

logger = logging.getLogger(__name__)

# Configuration files that mean a defect-finding tool is wired in. Style linters
# are deliberately absent — dev_tooling.py scores those, and formatting is not
# the same concern as defect detection.
_ANALYSIS_CONFIGS = {
    "SonarQube/SonarCloud": ["sonar-project.properties", ".sonarcloud.properties"],
    "clang-tidy": [".clang-tidy"],
    "Cppcheck": [".cppcheck-suppressions", "cppcheck-suppressions.txt"],
    "Semgrep": [".semgrep.yml", ".semgrep.yaml", ".semgrepignore"],
    "DeepSource": [".deepsource.toml"],
    "Codacy": [".codacy.yml", ".codacy.yaml"],
    "Coverity": [".coverity.yml", "cov-int"],
}

# Tool and sanitizer names to look for inside CI workflow definitions.
_ANALYSIS_IN_CI = {
    "SonarQube/SonarCloud": re.compile(r"\bsonar(?:cloud|qube|-scanner)?\b", re.I),
    "Coverity": re.compile(r"\bcoverity\b", re.I),
    "Cppcheck": re.compile(r"\bcppcheck\b", re.I),
    "Semgrep": re.compile(r"\bsemgrep\b", re.I),
    "clang-tidy": re.compile(r"\bclang-tidy\b", re.I),
    "scan-build": re.compile(r"\bscan-build\b", re.I),
    "Flawfinder": re.compile(r"\bflawfinder\b", re.I),
    "Sanitizers": re.compile(r"-fsanitize=|\b(?:asan|ubsan|tsan|msan)\b", re.I),
}

# Only workflows whose names suggest analysis are read, to bound the requests.
# "check" is deliberately absent: it matched linkchecker, markdown-link-check
# and review-checklist, which consumed the read budget before any workflow that
# actually builds the code.
_ANALYSIS_WORKFLOW_HINT = re.compile(
    r"(analy|lint|scan|secur|sanitiz|tidy|sonar|coverity|codeql|nightly|asan|ubsan)", re.I
)
_MAX_ANALYSIS_WORKFLOWS = 8

# Build-configuration files likely to carry hardening settings.
_BUILD_FILES = ["CMakeLists.txt", "configure.ac", "Makefile.am", "meson.build"]

# Larger projects keep compiler flags out of the root build file. Rather than
# guessing filenames per project — HDF5 puts its sanitizer setup in
# config/sanitizer/sanitizers.cmake — these conventional directories are listed
# and any file whose name suggests flags is read.
_FLAG_DIRECTORIES = ["cmake", "config/cmake", "config/sanitizer", "CMake"]
_FLAG_FILE_HINT = re.compile(r"(sanitiz|warn|flag|harden|secur)", re.I)
_MAX_FLAG_FILES = 4

_HARDENING_MARKERS = {
    "Warnings as errors": re.compile(r"-Werror\b"),
    "Fortify source": re.compile(r"_FORTIFY_SOURCE", re.I),
    "Stack protector": re.compile(r"-fstack-protector", re.I),
    "Sanitizers": re.compile(r"-fsanitize=", re.I),
    "CERT / MISRA reference": re.compile(r"\b(?:CERT[- ]?C\b|MISRA)\b", re.I),
}

# Labels projects use for defect reports, across the conventions in common use.
_DEFECT_LABELS = ["bug", "defect", "crash", "regression", "type: bug", "kind/bug",
                  "type/bug", "bug report"]

# GitHub issue *types* are a native field, separate from labels, and are what
# several of these projects actually use. HDF5 carries no bug label at all but
# has 479 Bug-typed issues, so a label-only query reported it as unmeasurable.
_DEFECT_ISSUE_TYPES = ["Bug", "Defect"]

_TREND_WINDOW_DAYS = 365
_MIN_TREND_VOLUME = 5          # below this the comparison is noise
_TREND_TOLERANCE = 1.25        # up to 25% growth still counts as stable

_MIN_ANALYSIS_TOOLS = 1
# Calibrated against the portfolio, not picked a priori: HDF5 shows one
# indicator (sanitizers), ADIOS2 and zfp none. These projects simply do not
# carry much hardening configuration, so the question worth asking is whether
# any secure-coding practice is in evidence at all.
_MIN_HARDENING_MARKERS = 1


class ReliabilityCollector(GitHubCollectorBase):
    """Collects static-analysis, hardening and defect-trend signals (Section 4.3.1)."""

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        repo_name = package.get("name", "Unknown")
        owner_repo = self._extract_owner_repo(package.get("repo_url", ""))
        if not owner_repo:
            logger.error(f"Could not extract owner/repo from {package.get('repo_url')}")
            return self._empty_result(repo_name)

        owner, repo = owner_repo
        logger.info(f"Collecting reliability metrics for {repo_name}")

        async with httpx.AsyncClient(timeout=30.0) as client:
            workflows = await self._read_analysis_workflows(client, owner, repo)
            tools, hardening, trend = await asyncio.gather(
                self._find_analysis_tools(client, owner, repo, workflows),
                self._find_hardening(client, owner, repo, workflows),
                self._defect_trend(client, owner, repo),
                return_exceptions=True,
            )

        if isinstance(tools, Exception):
            logger.warning(f"Analysis tool scan failed: {tools}")
            tools = []
        if isinstance(workflows, Exception):
            workflows = []
        if isinstance(hardening, Exception):
            logger.warning(f"Hardening scan failed: {hardening}")
            hardening = []
        if isinstance(trend, Exception):
            logger.warning(f"Defect trend failed: {trend}")
            trend = {"measurable": False, "recent": 0, "previous": 0, "direction": None}

        return {
            "package_name": repo_name,
            "repository": f"{owner}/{repo}",
            "timestamp": self._get_timestamp(),
            "analysis_tools": tools,
            "hardening": hardening,
            "defect_trend": trend,
            "overall_score": self._calculate_score(tools, hardening, trend),
        }

    # ------------------------------------------------------------------ fetch

    async def _find_analysis_tools(
        self, client: httpx.AsyncClient, owner: str, repo: str,
        workflows: List[str],
    ) -> List[str]:
        """Defect-finding tools, from config files and analysis-shaped workflows."""
        found = set()

        async def check(tool: str, paths: List[str]) -> Optional[str]:
            for path in paths:
                if await self._check_file_exists(client, owner, repo, path):
                    return tool
            return None

        results = await asyncio.gather(
            *[check(t, p) for t, p in _ANALYSIS_CONFIGS.items()],
            return_exceptions=True,
        )
        found.update(r for r in results if isinstance(r, str))

        for text in workflows:
            for tool, pattern in _ANALYSIS_IN_CI.items():
                if pattern.search(text):
                    found.add(tool)
        return sorted(found)

    async def _read_analysis_workflows(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> List[str]:
        """Text of the workflows whose names suggest they run analysis."""
        resp = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}/contents/.github/workflows",
            headers=self.github_headers,
        )
        if resp.status_code != 200:
            return []
        entries = resp.json()
        if not isinstance(entries, list):
            return []
        candidates = [
            e for e in entries
            if e.get("name", "").endswith((".yml", ".yaml"))
            and e.get("download_url")
            and _ANALYSIS_WORKFLOW_HINT.search(e["name"])
        ][:_MAX_ANALYSIS_WORKFLOWS]

        async def read(url: str) -> str:
            try:
                r = await client.get(url)
                return r.text if r.status_code == 200 else ""
            except Exception:
                return ""

        return [t for t in await asyncio.gather(*[read(e["download_url"]) for e in candidates]) if t]

    async def _find_hardening(
        self, client: httpx.AsyncClient, owner: str, repo: str,
        workflows: List[str],
    ) -> List[str]:
        """Secure-coding practice indicators in the build files and in CI.

        Large projects keep compiler flags out of the root build file — HDF5's
        live under config/cmake/ — and sanitizer runs are usually CI jobs rather
        than build settings, so both corpora are searched.
        """

        async def read(path: str) -> str:
            try:
                r = await client.get(
                    f"https://api.github.com/repos/{owner}/{repo}/contents/{path}",
                    headers=self.github_headers,
                )
                if r.status_code != 200:
                    return ""
                return base64.b64decode(r.json().get("content", "")).decode("utf-8", "replace")
            except Exception:
                return ""

        flag_paths = await self._find_flag_files(client, owner, repo)
        texts = await asyncio.gather(
            *[read(p) for p in _BUILD_FILES + flag_paths]
        )
        corpus = "\n".join([t for t in texts if t] + workflows)
        if not corpus:
            return []
        return [
            label for label, pattern in _HARDENING_MARKERS.items()
            if pattern.search(corpus)
        ]

    async def _find_flag_files(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> List[str]:
        """Paths of build-configuration files whose names suggest compiler flags."""

        async def listing(directory: str) -> List[str]:
            try:
                r = await client.get(
                    f"https://api.github.com/repos/{owner}/{repo}/contents/{directory}",
                    headers=self.github_headers,
                )
                if r.status_code != 200:
                    return []
                entries = r.json()
                if not isinstance(entries, list):
                    return []
                return [
                    e["path"] for e in entries
                    if e.get("type") == "file" and _FLAG_FILE_HINT.search(e.get("name", ""))
                ]
            except Exception:
                return []

        results = await asyncio.gather(*[listing(d) for d in _FLAG_DIRECTORIES])
        paths: List[str] = []
        for group in results:
            paths.extend(group)
        return paths[:_MAX_FLAG_FILES]

    async def _defect_trend(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> Dict[str, Any]:
        """Defect reports opened in the last year against the year before.

        Returns measurable=False when the project labels no defects at all —
        HDF5 labels by component, and reporting "0 vs 0, stable" would be a
        fabricated pass.
        """
        today = datetime.now(timezone.utc).date()
        recent_start = today - timedelta(days=_TREND_WINDOW_DAYS)
        prev_start = today - timedelta(days=_TREND_WINDOW_DAYS * 2)

        # A label containing a space or colon has to be quoted, or the search
        # parser splits it and silently drops the rest of the label list —
        # which returned 0 for every project until it was caught.
        labels = ",".join(
            f'"{l}"' if (" " in l or ":" in l) else l for l in _DEFECT_LABELS
        )

        async def count(qualifier: str, date_range: str) -> int:
            q = f'repo:{owner}/{repo} is:issue {qualifier} created:{date_range}'
            r = await search_get(
                client,
                f"https://api.github.com/search/issues?q={quote(q)}&per_page=1",
                self.github_headers,
            )
            return r.json().get("total_count", 0) if r else 0

        recent_range = f"{recent_start}..{today}"
        prev_range = f"{prev_start}..{recent_start}"

        # Issue types first, since a project using them generally does not also
        # label defects; fall back to labels only when types yield nothing.
        type_expr = ",".join(_DEFECT_ISSUE_TYPES)
        recent, previous = await asyncio.gather(
            count(f"type:{type_expr}", recent_range),
            count(f"type:{type_expr}", prev_range),
        )
        source = "issue type"

        if recent + previous == 0:
            # Comma-separated values in a label: qualifier are ORed, so one
            # query covers every convention in _DEFECT_LABELS.
            recent, previous = await asyncio.gather(
                count(f"label:{labels}", recent_range),
                count(f"label:{labels}", prev_range),
            )
            source = "label"

        if recent + previous < _MIN_TREND_VOLUME:
            return {"measurable": False, "recent": recent, "previous": previous,
                    "direction": None, "source": source}

        if previous == 0:
            direction = "increasing"
        else:
            ratio = recent / previous
            direction = ("stable" if ratio <= _TREND_TOLERANCE
                         else "increasing")
            if ratio < 0.75:
                direction = "improving"
        return {"measurable": True, "recent": recent, "previous": previous,
                "direction": direction, "source": source}

    # ---------------------------------------------------------------- scoring

    def _calculate_score(
        self, tools: List[str], hardening: List[str], trend: Dict
    ) -> Dict[str, Any]:
        sub: Dict[str, Dict[str, Any]] = {}

        sub["advanced_static_analysis"] = {
            "label": "Advanced Static Analysis",
            "value": ", ".join(tools) if tools
                     else "No defect-analysis tooling found beyond CodeQL",
            "passing": len(tools) >= _MIN_ANALYSIS_TOOLS,
        }

        sub["cert_compliance"] = {
            "label": "CERT Guidelines Compliance",
            "value": f"{len(hardening)} secure-coding indicator"
                     f"{'s' if len(hardening) != 1 else ''}: " + ", ".join(hardening)
                     if hardening else "No hardening settings found in the build files or CI",
            "detail": "Practice indicators, not audited conformance",
            "passing": len(hardening) >= _MIN_HARDENING_MARKERS,
        }

        if not trend.get("measurable"):
            value = "Project does not record defect reports by type or label"
            passing = False
        else:
            value = (f"{trend['recent']} defect reports in the last year vs "
                     f"{trend['previous']} the year before ({trend['direction']}, "
                     f"by {trend.get('source', 'label')})")
            passing = trend["direction"] in ("stable", "improving")
        sub["reliability_trend"] = {
            "label": "Reliability Trend Analysis",
            "value": value,
            "passing": passing,
        }

        score = sum(1 for s in sub.values() if s["passing"])
        return {
            "score": score,
            "max_score": len(sub),
            "percentage": round(score / len(sub) * 100, 2),
            "sub_scores": sub,
        }

    def _empty_result(self, repo_name: str) -> Dict[str, Any]:
        trend = {"measurable": False, "recent": 0, "previous": 0,
                 "direction": None, "source": "label"}
        return {
            "package_name": repo_name,
            "repository": "unknown",
            "timestamp": self._get_timestamp(),
            "analysis_tools": [],
            "hardening": [],
            "defect_trend": trend,
            "overall_score": self._calculate_score([], [], trend),
        }
