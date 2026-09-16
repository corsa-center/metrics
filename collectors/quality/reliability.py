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
from collectors.ecosystem.base import COLLECTION_GAP, GitHubCollectorBase, RetryingTransport

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

        async with httpx.AsyncClient(timeout=30.0, transport=RetryingTransport()) as client:
            workflows, workflows_gap = await self._read_analysis_workflows(client, owner, repo)
            results = await asyncio.gather(
                self._find_analysis_tools(client, owner, repo, workflows),
                self._find_hardening(client, owner, repo, workflows),
                self._defect_trend(client, owner, repo),
                return_exceptions=True,
            )

        if isinstance(results[0], Exception):
            logger.warning(f"COLLECTION-GAP category=analysis_tools reason=exception:{results[0]!r}")
            tools, tools_gap = [], True
        else:
            tools, tools_gap = results[0]

        if isinstance(results[1], Exception):
            logger.warning(f"COLLECTION-GAP category=hardening reason=exception:{results[1]!r}")
            hardening, hardening_gap = [], True
        else:
            hardening, hardening_gap = results[1]

        if isinstance(results[2], Exception):
            logger.warning(f"COLLECTION-GAP category=defect_trend reason=exception:{results[2]!r}")
            trend = {"measurable": False, "recent": 0, "previous": 0,
                     "direction": None, "not_collected": True}
        else:
            trend = results[2]

        tools_gap = tools_gap or workflows_gap
        hardening_gap = hardening_gap or workflows_gap

        return {
            "package_name": repo_name,
            "repository": f"{owner}/{repo}",
            "timestamp": self._get_timestamp(),
            "analysis_tools": tools,
            "hardening": hardening,
            "defect_trend": trend,
            "overall_score": self._calculate_score(tools, hardening, trend, tools_gap, hardening_gap),
        }

    # ------------------------------------------------------------------ fetch

    async def _find_analysis_tools(
        self, client: httpx.AsyncClient, owner: str, repo: str,
        workflows: List[str],
    ) -> tuple:
        """Defect-finding tools, from config files and analysis-shaped workflows.

        Returns (sorted tool names, saw_gap). A tool found via a config file
        or in the CI text is real regardless of gaps elsewhere; saw_gap only
        matters to the caller when the result is otherwise empty.
        """
        found = set()
        saw_gap = False

        async def check(tool: str, paths: List[str]) -> tuple:
            gap = False
            for path in paths:
                result = await self._check_file_exists(client, owner, repo, path)
                if result is COLLECTION_GAP:
                    gap = True
                    continue
                if result:
                    return tool, gap
            return None, gap

        results = await asyncio.gather(*[check(t, p) for t, p in _ANALYSIS_CONFIGS.items()])
        for tool, gap in results:
            if tool:
                found.add(tool)
            elif gap:
                saw_gap = True

        for text in workflows:
            for tool, pattern in _ANALYSIS_IN_CI.items():
                if pattern.search(text):
                    found.add(tool)
        return sorted(found), saw_gap

    async def _read_analysis_workflows(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> tuple:
        """Text of the workflows whose names suggest they run analysis, and
        whether the directory listing (or any candidate read) gapped.
        """
        entries = await self._github_get(
            client, f"https://api.github.com/repos/{owner}/{repo}/contents/.github/workflows",
        )
        if entries is COLLECTION_GAP:
            return [], True
        if not isinstance(entries, list):
            return [], False
        candidates = [
            e for e in entries
            if e.get("name", "").endswith((".yml", ".yaml"))
            and e.get("download_url")
            and _ANALYSIS_WORKFLOW_HINT.search(e["name"])
        ][:_MAX_ANALYSIS_WORKFLOWS]

        async def read(url: str) -> Optional[str]:
            try:
                r = await client.get(url)
                return r.text if r.status_code == 200 else None
            except Exception:
                return None

        texts = await asyncio.gather(*[read(e["download_url"]) for e in candidates])
        saw_gap = any(t is None for t in texts)
        return [t for t in texts if t], saw_gap

    async def _find_hardening(
        self, client: httpx.AsyncClient, owner: str, repo: str,
        workflows: List[str],
    ) -> tuple:
        """Secure-coding practice indicators in the build files and in CI.

        Large projects keep compiler flags out of the root build file — HDF5's
        live under config/cmake/ — and sanitizer runs are usually CI jobs rather
        than build settings, so both corpora are searched. Returns (markers
        found, saw_gap): a marker actually found is real regardless of gaps
        elsewhere, but an empty result needs saw_gap to tell "no hardening
        configured" from "couldn't read enough of the repo to tell".
        """

        async def read(path: str):
            data = await self._github_get(
                client, f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
            )
            if data is COLLECTION_GAP:
                return COLLECTION_GAP
            if data is None:
                return ""
            return base64.b64decode(data.get("content", "")).decode("utf-8", "replace")

        flag_paths, flag_gap = await self._find_flag_files(client, owner, repo)
        texts = await asyncio.gather(
            *[read(p) for p in _BUILD_FILES + flag_paths]
        )
        saw_gap = flag_gap or any(t is COLLECTION_GAP for t in texts)
        # COLLECTION_GAP is falsy, same as "", so this filter drops both.
        corpus = "\n".join([t for t in texts if t] + workflows)
        if not corpus:
            return [], saw_gap
        return [
            label for label, pattern in _HARDENING_MARKERS.items()
            if pattern.search(corpus)
        ], saw_gap

    async def _find_flag_files(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> tuple:
        """Paths of build-configuration files whose names suggest compiler
        flags, and whether any directory listing gapped.
        """

        async def listing(directory: str) -> tuple:
            data = await self._github_get(
                client, f"https://api.github.com/repos/{owner}/{repo}/contents/{directory}"
            )
            if data is COLLECTION_GAP:
                return [], True
            if not isinstance(data, list):
                return [], False
            return [
                e["path"] for e in data
                if e.get("type") == "file" and _FLAG_FILE_HINT.search(e.get("name", ""))
            ], False

        results = await asyncio.gather(*[listing(d) for d in _FLAG_DIRECTORIES])
        paths: List[str] = []
        saw_gap = False
        for group_paths, gap in results:
            paths.extend(group_paths)
            saw_gap = saw_gap or gap
        return paths[:_MAX_FLAG_FILES], saw_gap

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

        async def count(qualifier: str, date_range: str) -> tuple:
            q = f'repo:{owner}/{repo} is:issue {qualifier} created:{date_range}'
            r = await search_get(
                client,
                f"https://api.github.com/search/issues?q={quote(q)}&per_page=1",
                self.github_headers,
            )
            if r is None:
                # Exhausted retries or a non-200: we don't know the real
                # count, so this is not the same as a confirmed 0.
                return 0, True
            return r.json().get("total_count", 0), False

        recent_range = f"{recent_start}..{today}"
        prev_range = f"{prev_start}..{recent_start}"

        # Issue types first, since a project using them generally does not also
        # label defects; fall back to labels only when types yield nothing.
        type_expr = ",".join(_DEFECT_ISSUE_TYPES)
        (recent, recent_gap), (previous, previous_gap) = await asyncio.gather(
            count(f"type:{type_expr}", recent_range),
            count(f"type:{type_expr}", prev_range),
        )
        saw_gap = recent_gap or previous_gap
        source = "issue type"

        if recent + previous == 0:
            # Comma-separated values in a label: qualifier are ORed, so one
            # query covers every convention in _DEFECT_LABELS. Attempted even
            # if the type search gapped, since it's an independent query --
            # any gap it hits is merged into saw_gap below either way.
            (recent, recent_gap), (previous, previous_gap) = await asyncio.gather(
                count(f"label:{labels}", recent_range),
                count(f"label:{labels}", prev_range),
            )
            saw_gap = saw_gap or recent_gap or previous_gap
            source = "label"

        if saw_gap:
            return {"measurable": False, "recent": recent, "previous": previous,
                    "direction": None, "source": source, "not_collected": True}

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
        self, tools: List[str], hardening: List[str], trend: Dict,
        tools_gap: bool = False, hardening_gap: bool = False,
    ) -> Dict[str, Any]:
        sub: Dict[str, Dict[str, Any]] = {}

        analysis_entry: Dict[str, Any] = {
            "label": "Advanced Static Analysis",
            "value": ", ".join(tools) if tools
                     else "No defect-analysis tooling found beyond CodeQL",
            "passing": len(tools) >= _MIN_ANALYSIS_TOOLS,
        }
        # An empty result built on a gap isn't a confirmed "no tooling" --
        # a found tool stands regardless, since it came from data that did
        # come back.
        if not tools and tools_gap:
            analysis_entry["not_collected"] = True
        sub["advanced_static_analysis"] = analysis_entry

        cert_entry: Dict[str, Any] = {
            "label": "CERT Guidelines Compliance",
            "value": f"{len(hardening)} secure-coding indicator"
                     f"{'s' if len(hardening) != 1 else ''}: " + ", ".join(hardening)
                     if hardening else "No hardening settings found in the build files or CI",
            "detail": "Practice indicators, not audited conformance",
            "passing": len(hardening) >= _MIN_HARDENING_MARKERS,
        }
        if not hardening and hardening_gap:
            cert_entry["not_collected"] = True
        sub["cert_compliance"] = cert_entry

        if trend.get("not_collected"):
            value = "Defect trend could not be measured (search rate limited)"
            passing = False
        elif not trend.get("measurable"):
            value = "Project does not record defect reports by type or label"
            passing = False
        else:
            value = (f"{trend['recent']} defect reports in the last year vs "
                     f"{trend['previous']} the year before ({trend['direction']}, "
                     f"by {trend.get('source', 'label')})")
            passing = trend["direction"] in ("stable", "improving")
        trend_entry: Dict[str, Any] = {
            "label": "Reliability Trend Analysis",
            "value": value,
            "passing": passing,
        }
        if trend.get("not_collected"):
            trend_entry["not_collected"] = True
        sub["reliability_trend"] = trend_entry

        scorable = {k: v for k, v in sub.items() if not v.get("not_collected")}
        score = sum(1 for s in scorable.values() if s["passing"])
        max_score = len(scorable)
        if not max_score:
            return {
                "score": None, "max_score": 0, "percentage": None,
                "status": "not_collected", "sub_scores": sub,
            }
        return {
            "score": score,
            "max_score": max_score,
            "percentage": round(score / max_score * 100, 2),
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
