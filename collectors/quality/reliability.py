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
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx

from collectors.rate_limit import search_get
from collectors.ecosystem.base import (
    _VENDORED_DIR, COLLECTION_GAP, GitHubCollectorBase, RepoTree, RetryingTransport, get_threshold,
)

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

# Workflows whose names suggest analysis are read first; the rest of the
# request budget then fills with whatever workflows remain (see
# _read_analysis_workflows). Previously this was the ONLY thing read, which
# meant a repo naming its per-compiler CI jobs generically -- AMReX's gcc.yml,
# clang.yml, cuda.yml, hip.yml -- had zero of its workflows read at all
# (METRIC_BLIND_SPOTS.md class F4). "check" is deliberately absent from the
# hint: it matched linkchecker, markdown-link-check and review-checklist,
# which used to consume the whole (smaller) budget on its own.
_ANALYSIS_WORKFLOW_HINT = re.compile(
    r"(analy|lint|scan|secur|sanitiz|tidy|sonar|coverity|codeql|nightly|asan|ubsan)", re.I
)
_MAX_ANALYSIS_WORKFLOWS = 25

# Build-configuration files likely to carry hardening settings.
_BUILD_FILES = ["CMakeLists.txt", "configure.ac", "Makefile.am", "meson.build"]

# Larger projects keep compiler flags out of the root build file -- AMReX's
# live in Tools/CMake/, HDF5's in config/flags/. Rather than guessing which
# directories to list, any file anywhere in the tree whose name suggests
# flags is read.
_FLAG_FILE_HINT = re.compile(r"(sanitiz|warn|flag|harden|secur)", re.I)
# Compiler-setup modules carry flags too, under names the hint above never
# matches -- SUNDIALS sets -Werror and -fsanitize=address in
# cmake/SundialsSetupCompilers.cmake. Read after the stronger hints, so
# they can't crowd those out of the cap.
_COMPILER_FILE_HINT = re.compile(r"compil", re.I)
_MAX_FLAG_FILES = 6

_HARDENING_MARKERS = {
    # CMAKE_COMPILE_WARNING_AS_ERROR is CMake's native switch (3.24+).
    "Warnings as errors": re.compile(r"-Werror\b|COMPILE_WARNING_AS_ERROR\b|WARNINGS_AS_ERRORS\b"),
    "Fortify source": re.compile(r"_FORTIFY_SOURCE", re.I),
    "Stack protector": re.compile(r"-fstack-protector", re.I),
    # Also the CMake options projects expose for them
    # (SUNDIALS_ENABLE_ADDRESS_SANITIZER, ENABLE_UBSAN, ...).
    "Sanitizers": re.compile(
        r"-fsanitize=|\b\w*(?:ADDRESS|MEMORY|LEAK|THREAD|UNDEFINED(?:_BEHAVIOR)?)_SANITIZER\b"
        r"|ENABLE_[AUMT]SAN\b",
        re.I,
    ),
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


def _binomial_tail(recent: int, total: int, direction: str) -> float:
    """One-sided p-value for `recent` of `total` defect reports falling in the
    recent window, if the defect rate were unchanged (each report equally
    likely to land in either window)."""
    ks = range(recent, total + 1) if direction == "increasing" else range(0, recent + 1)
    return sum(math.comb(total, k) for k in ks) / 2 ** total


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
            tree = await RepoTree.fetch(client, self.github_headers, owner, repo)
            workflows, workflows_gap = await self._read_analysis_workflows(client, owner, repo, tree)
            results = await asyncio.gather(
                self._find_analysis_tools(tree, workflows),
                self._find_hardening(client, owner, repo, tree, workflows),
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

    async def _find_analysis_tools(self, tree, workflows: List[str]) -> tuple:
        """Defect-finding tools, from config files and analysis-shaped workflows.

        Returns (sorted tool names, saw_gap). A tool found via a config file
        or in the CI text is real regardless of gaps elsewhere; saw_gap only
        matters to the caller when the result is otherwise empty. Config
        files are matched against a RepoTree (case-insensitive, one fetch)
        rather than probed one literal path at a time -- see
        METRIC_BLIND_SPOTS.md class F1.
        """
        found = set()
        saw_gap = tree is COLLECTION_GAP

        if not saw_gap:
            for tool, paths in _ANALYSIS_CONFIGS.items():
                if tree.match(paths):
                    found.add(tool)

        for text in workflows:
            for tool, pattern in _ANALYSIS_IN_CI.items():
                if pattern.search(text):
                    found.add(tool)
        return sorted(found), saw_gap

    async def _read_analysis_workflows(
        self, client: httpx.AsyncClient, owner: str, repo: str, tree
    ) -> tuple:
        """Text of up to _MAX_ANALYSIS_WORKFLOWS workflow files, and whether
        any candidate read gapped.

        Workflows whose name suggests analysis are read first; the rest of
        the budget is then filled with whatever workflows remain, rather
        than reading only keyword-matched names. A repo whose per-compiler
        jobs are named generically (AMReX's gcc.yml, cuda.yml, hip.yml) used
        to have zero of its workflows read at all -- this still prioritizes
        the likely-relevant ones, but no longer reads nothing when the
        naming convention doesn't cooperate (METRIC_BLIND_SPOTS.md class F4).
        """
        if tree is COLLECTION_GAP:
            return [], True
        all_workflows = tree.find(r"^\.github/workflows/.*\.ya?ml$")
        hinted = [w for w in all_workflows if _ANALYSIS_WORKFLOW_HINT.search(w.rsplit("/", 1)[-1])]
        rest = [w for w in all_workflows if w not in hinted]
        candidates = (hinted + rest)[:_MAX_ANALYSIS_WORKFLOWS]
        # More workflows exist than the cap allows reading: an empty result
        # from what follows isn't a confirmed absence, since the unread
        # remainder could hold the marker being searched for (HDF5 has 76
        # workflows; this repo's cap only reaches 25 of them).
        truncated = len(all_workflows) > len(candidates)

        async def read(path: str) -> Optional[str]:
            data = await self._github_get(
                client, f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
            )
            if data is COLLECTION_GAP:
                return None
            if data is None:
                return ""
            return base64.b64decode(data.get("content", "")).decode("utf-8", "replace")

        texts = await asyncio.gather(*[read(p) for p in candidates])
        saw_gap = truncated or any(t is None for t in texts)
        return [t for t in texts if t], saw_gap

    async def _find_hardening(
        self, client: httpx.AsyncClient, owner: str, repo: str,
        tree, workflows: List[str],
    ) -> tuple:
        """Secure-coding practice indicators in the build files and in CI.

        Large projects keep compiler flags out of the root build file — AMReX's
        live under Tools/CMake/, HDF5's under config/flags/ — and sanitizer runs
        are usually CI jobs rather than build settings, so both corpora are
        searched. Returns (markers found, saw_gap): a marker actually found is
        real regardless of gaps elsewhere, but an empty result needs saw_gap
        to tell "no hardening configured" from "couldn't read enough of the
        repo to tell".
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

        flag_paths, flag_gap = self._find_flag_files(tree)
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

    def _find_flag_files(self, tree) -> tuple:
        """Paths of .cmake files whose names suggest compiler flags, searched
        across the whole tree rather than four fixed directories -- AMReX
        keeps its flags in Tools/CMake/, which a directory allowlist never
        reached even though a real hardening setting (-Werror in
        AMReXFlagsTargets.cmake) was sitting right there
        (corsa-center/metrics#51, METRIC_BLIND_SPOTS.md class F3).

        Restricted to .cmake specifically (not any file with a flag-shaped
        name) so the small result cap isn't spent on false positives a
        whole-tree search otherwise turns up -- SECURITY.md ("secur") and a
        CI workflow named flag_prs_to_master.yml ("flag") both matched
        _FLAG_FILE_HINT on Trilinos and would have crowded out its real
        TriBITS compiler-flag .cmake files.
        """
        if tree is COLLECTION_GAP:
            return [], True
        cmake = [p for p in tree.paths if p.endswith(".cmake") and not _VENDORED_DIR.search(p)]
        strong = [p for p in cmake if _FLAG_FILE_HINT.search(p.rsplit("/", 1)[-1])]
        compiler = [p for p in cmake if p not in strong
                    and _COMPILER_FILE_HINT.search(p.rsplit("/", 1)[-1])]
        compiler.sort(key=lambda p: (p.count("/"), p))
        return (strong + compiler)[:_MAX_FLAG_FILES], False

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
        # label defects.
        type_expr = ",".join(_DEFECT_ISSUE_TYPES)
        (recent, recent_gap), (previous, previous_gap) = await asyncio.gather(
            count(f"type:{type_expr}", recent_range),
            count(f"type:{type_expr}", prev_range),
        )
        saw_gap = recent_gap or previous_gap
        source = "issue type"

        # Fall back to labels whenever types alone can't carry the comparison,
        # not only when they return exactly zero. A single natively-typed
        # issue used to suppress the fallback entirely: AMReX has one typed
        # issue and 35 bug-labelled ones, and reported "does not record defect
        # reports by type or label" on the strength of that one. Projects
        # migrating to issue types have both conventions in play at once.
        min_volume = get_threshold("4.3.1", "Reliability Trend Analysis", "min_trend_volume")
        if recent + previous < min_volume:
            # Comma-separated values in a label: qualifier are ORed, so one
            # query covers every convention in _DEFECT_LABELS. Attempted even
            # if the type search gapped, since it's an independent query --
            # any gap it hits is merged into saw_gap below either way.
            (label_recent, recent_gap), (label_previous, previous_gap) = await asyncio.gather(
                count(f"label:{labels}", recent_range),
                count(f"label:{labels}", prev_range),
            )
            saw_gap = saw_gap or recent_gap or previous_gap
            # Keep whichever convention actually carries the project's
            # defects, rather than assuming the second query supersedes.
            if label_recent + label_previous > recent + previous:
                recent, previous = label_recent, label_previous
                source = "label"

        if saw_gap:
            return {"measurable": False, "recent": recent, "previous": previous,
                    "direction": None, "source": source, "not_collected": True}

        if recent + previous < get_threshold("4.3.1", "Reliability Trend Analysis", "min_trend_volume"):
            return {"measurable": False, "recent": recent, "previous": previous,
                    "direction": None, "source": source}

        if previous == 0:
            direction = "increasing"
        else:
            ratio = recent / previous
            direction = ("stable" if ratio <= get_threshold("4.3.1", "Reliability Trend Analysis", "trend_tolerance")
                         else "increasing")
            if ratio < 0.75:
                direction = "improving"
        # At these volumes a large ratio is often chance: 18 vs 11 is within
        # normal year-to-year variation. Only call a trend when the split
        # between the two windows is unlikely under an unchanged rate.
        within_variation = (direction != "stable" and _binomial_tail(recent, recent + previous, direction)
                            >= get_threshold("4.3.1", "Reliability Trend Analysis", "significance"))
        if within_variation:
            direction = "stable"
        return {"measurable": True, "recent": recent, "previous": previous,
                "direction": direction, "source": source,
                "within_normal_variation": within_variation}

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
            "passing": len(tools) >= get_threshold("4.3.1", "Advanced Static Analysis"),
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
            "passing": len(hardening) >= get_threshold("4.3.1", "CERT Guidelines Compliance"),
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
            direction = trend["direction"]
            if trend.get("within_normal_variation"):
                direction += " -- change within normal variation"
            value = (f"{trend['recent']} defect reports in the last year vs "
                     f"{trend['previous']} the year before ({direction}, "
                     f"by {trend.get('source', 'label')})")
            passing = trend["direction"] in get_threshold("4.3.1", "Reliability Trend Analysis", "passing_directions")
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
