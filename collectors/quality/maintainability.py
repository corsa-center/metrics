"""
Maintainability Collector (CASS Report Section 4.3.6)

Fills the four sub-metrics that had no data, from three cheap GitHub calls:
the recursive git tree, the language breakdown, and recent commit messages.

  - Advanced Complexity Analysis     : source file size distribution and nesting
  - Code Quality Assessment          : test-to-source file ratio
  - Documentation Quality Evaluation : doc coverage and API doc generation
  - Refactoring and Evolution Tracking : refactor intent in recent commits

Knowledge Distribution Analysis (bus factor) is rendered by the orchestrator
from ActiveMaintenanceCollector's data and is not repeated here.

On complexity: the report asks for static analysis of computational complexity.
That needs the source checked out and a tool run over it. What this collector
reports is a *structural* proxy — how large the source files are and how deeply
nested the tree is — which is honest about being a proxy and costs one API call
instead of a clone.
"""

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional, Set

import httpx

from collectors.sustainability.base import GitHubCollectorBase

logger = logging.getLogger(__name__)

_SOURCE_EXTENSIONS = {
    ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hxx", ".f", ".f90", ".f03",
    ".for", ".py", ".java", ".jl", ".rs", ".go", ".cu", ".cuh", ".m", ".r",
    ".js", ".ts", ".rb", ".scala", ".swift",
}
# Prose formats only. Counting everything under a doc/ directory swept in
# images and example source — HDF5's docs tree alone holds 165 .gif, 82 .png
# and 33 .c files, which inflated its documentation count by a factor of two.
_DOC_EXTENSIONS = {".md", ".rst", ".adoc", ".tex", ".dox"}
# .txt is documentation at the repo root (INSTALL.txt, RELEASE.txt) but is
# mostly reference fixtures deeper in the tree, so it is accepted only there
# or under an explicit doc directory.
_CONTEXTUAL_DOC_EXTENSIONS = {".txt"}

_TEST_PATH = re.compile(r"(^|/)(tests?|testing|unittests?)(/|$)", re.IGNORECASE)
_TEST_FILE = re.compile(r"(^|/)(test_[^/]+|[^/]+_test)\.[a-z0-9]+$", re.IGNORECASE)
_DOC_PATH = re.compile(r"(^|/)(docs?|documentation)(/|$)", re.IGNORECASE)

# Configuration for a documentation generator — the difference between having
# prose lying around and publishing real API documentation.
_DOC_GENERATORS = {
    "Doxygen": re.compile(r"(^|/)Doxyfile(\.[^/]+)?$", re.IGNORECASE),
    "Sphinx": re.compile(r"(^|/)(docs?|documentation)/(source/)?conf\.py$", re.IGNORECASE),
    "MkDocs": re.compile(r"(^|/)mkdocs\.ya?ml$", re.IGNORECASE),
    "Javadoc/Gradle": re.compile(r"(^|/)javadoc\.gradle$", re.IGNORECASE),
}

# Commit subjects that indicate deliberate structural work. "remove" alone is
# deliberately absent — it matches bug fixes like "Remove abort on infinite
# loop", which is not refactoring.
_REFACTOR_INTENT = re.compile(
    r"\b(refactor\w*|cleanup|clean[- ]up|simplif\w+|deprecat\w+|restructur\w+"
    r"|rework\w*|moderni[sz]\w+|tidy|dead code"
    r"|remove (?:unused|dead|obsolete|redundant|noop|no-op|legacy|stale))\b",
    re.IGNORECASE,
)

# A source file past this size is hard to reason about in one sitting.
_LARGE_FILE_BYTES = 100_000
# Codebases where more than this share of source files are oversized read as
# structurally heavy. A line on a continuum: HDF5 sits at 5.1%, ADIOS2 at 2.2%,
# zfp at 0.0%, so this separates the portfolio roughly where it naturally splits.
_MAX_LARGE_FILE_SHARE = 0.05
# Deep trees are harder to navigate; 10 levels is already generous.
_MAX_TREE_DEPTH = 10

# One test file per five source files.
_MIN_TEST_RATIO = 0.20
# Documentation files as a share of source files.
_MIN_DOC_RATIO = 0.05
# Share of recent commits showing refactoring intent. Calibrated against the
# portfolio rather than picked a priori: measured over 300 commits, HDF5 runs
# 2.7%, ADIOS2 2.0% and zfp 3.0%. A 3% bar would fail two of the three for
# what is ordinary, healthy maintenance, so the question this asks is whether
# sustained structural work happens at all, not whether it is unusually common.
_MIN_REFACTOR_SHARE = 0.02

# Sampled across three pages. At 100 commits each commit is worth a full
# percentage point, which makes a 3% threshold indistinguishable from noise.
_COMMIT_SAMPLE = 100
_COMMIT_PAGES = 3


class MaintainabilityCollector(GitHubCollectorBase):
    """Collects maintainability and understandability signals (Section 4.3.6)."""

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        repo_name = package.get("name", "Unknown")
        owner_repo = self._extract_owner_repo(package.get("repo_url", ""))
        if not owner_repo:
            logger.error(f"Could not extract owner/repo from {package.get('repo_url')}")
            return self._empty_result(repo_name)

        owner, repo = owner_repo
        logger.info(f"Collecting maintainability metrics for {repo_name}")

        async with httpx.AsyncClient(timeout=60.0) as client:
            tree, languages, refactor = await asyncio.gather(
                self._get_tree(client, owner, repo),
                self._get_languages(client, owner, repo),
                self._get_refactor_activity(client, owner, repo),
                return_exceptions=True,
            )

        if isinstance(tree, Exception):
            logger.warning(f"Tree fetch failed: {tree}")
            tree = {"files": [], "truncated": False}
        if isinstance(languages, Exception):
            logger.warning(f"Language fetch failed: {languages}")
            languages = {}
        if isinstance(refactor, Exception):
            logger.warning(f"Commit scan failed: {refactor}")
            refactor = {"sampled": 0, "refactor_commits": 0, "share": None}

        composition = self._analyze_tree(tree["files"])
        composition["truncated"] = tree["truncated"]
        composition["languages"] = sorted(languages, key=languages.get, reverse=True)[:5]

        return {
            "package_name": repo_name,
            "repository": f"{owner}/{repo}",
            "timestamp": self._get_timestamp(),
            "composition": composition,
            "refactoring": refactor,
            "overall_score": self._calculate_score(composition, refactor),
        }

    # ------------------------------------------------------------------ fetch

    async def _get_tree(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> Dict[str, Any]:
        """Whole file layout in one call.

        GitHub truncates the response for very large repositories; the flag is
        carried through so downstream ratios can be reported as approximate
        rather than silently wrong.
        """
        resp = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}/git/trees/HEAD?recursive=1",
            headers=self.github_headers,
        )
        if resp.status_code != 200:
            return {"files": [], "truncated": False}
        data = resp.json()
        files = [
            {"path": e["path"], "size": e.get("size", 0)}
            for e in data.get("tree", [])
            if e.get("type") == "blob"
        ]
        return {"files": files, "truncated": bool(data.get("truncated"))}

    async def _get_languages(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> Dict[str, int]:
        resp = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}/languages",
            headers=self.github_headers,
        )
        return resp.json() if resp.status_code == 200 else {}

    async def _get_refactor_activity(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> Dict[str, Any]:
        """Refactoring intent across the most recent commits.

        Commit subjects are used rather than the code-frequency statistics API,
        which returns HTTP 422 for any repository over 10,000 commits — that
        rules out most of this portfolio (HDF5 alone has ~24,500).
        """
        subjects: List[str] = []
        for page in range(1, _COMMIT_PAGES + 1):
            resp = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/commits"
                f"?per_page={_COMMIT_SAMPLE}&page={page}",
                headers=self.github_headers,
            )
            if resp.status_code != 200:
                break
            batch = resp.json()
            if not isinstance(batch, list) or not batch:
                break
            subjects.extend(
                (c.get("commit", {}).get("message") or "").split("\n")[0] for c in batch
            )
        if not subjects:
            return {"sampled": 0, "refactor_commits": 0, "share": None}

        matches = [s for s in subjects if _REFACTOR_INTENT.search(s)]
        return {
            "sampled": len(subjects),
            "refactor_commits": len(matches),
            "share": round(len(matches) / len(subjects), 3),
            "examples": matches[:3],
        }

    # ---------------------------------------------------------------- analyze

    def _analyze_tree(self, files: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Classify the tree into source, test and documentation files."""
        source, tests, docs = [], [], []
        max_depth = 0
        generators: Set[str] = set()

        for f in files:
            path = f["path"]
            max_depth = max(max_depth, path.count("/") + 1)

            for name, pattern in _DOC_GENERATORS.items():
                if pattern.search(path):
                    generators.add(name)

            ext = ("." + path.rsplit(".", 1)[-1].lower()) if "." in path else ""
            is_test = bool(_TEST_PATH.search(path) or _TEST_FILE.search(path))

            if ext in _SOURCE_EXTENSIONS:
                # A test file is not also counted as source, so the ratio below
                # compares tests against the code they actually cover.
                (tests if is_test else source).append(f)
            elif is_test:
                # Fixtures and expected-output files under a test tree are not
                # documentation, whatever their extension.
                continue
            elif ext in _DOC_EXTENSIONS:
                docs.append(f)
            elif ext in _CONTEXTUAL_DOC_EXTENSIONS and (
                _DOC_PATH.search(path) or "/" not in path
            ):
                docs.append(f)

        sizes = [f["size"] for f in source]
        large = [s for s in sizes if s > _LARGE_FILE_BYTES]
        return {
            "source_files": len(source),
            "test_files": len(tests),
            "doc_files": len(docs),
            "max_depth": max_depth,
            "mean_source_bytes": round(sum(sizes) / len(sizes)) if sizes else 0,
            "largest_source_bytes": max(sizes, default=0),
            "large_file_share": round(len(large) / len(sizes), 3) if sizes else 0.0,
            "doc_generators": sorted(generators),
        }

    def _calculate_score(self, comp: Dict, refactor: Dict) -> Dict[str, Any]:
        sub: Dict[str, Dict[str, Any]] = {}
        approx = " (approximate — tree truncated)" if comp.get("truncated") else ""

        source_files = comp.get("source_files", 0)
        large_share = comp.get("large_file_share", 0.0)
        depth = comp.get("max_depth", 0)
        manageable = (
            source_files > 0
            and large_share <= _MAX_LARGE_FILE_SHARE
            and depth <= _MAX_TREE_DEPTH
        )
        sub["complexity_analysis"] = {
            "label": "Advanced Complexity Analysis",
            "value": (
                f"{source_files:,} source files, mean "
                f"{comp.get('mean_source_bytes', 0) / 1024:.1f} KB, "
                f"{large_share * 100:.1f}% over 100 KB, depth {depth}{approx}"
                if source_files else "No source files identified"
            ),
            "detail": "Structural proxy: file size and nesting, not static analysis",
            "passing": manageable,
        }

        test_ratio = (
            comp.get("test_files", 0) / source_files if source_files else 0.0
        )
        sub["code_quality"] = {
            "label": "Code Quality Assessment",
            "value": f"{comp.get('test_files', 0):,} test files for "
                     f"{source_files:,} source files ({test_ratio:.2f} ratio){approx}"
                     if source_files else "No source files identified",
            "passing": test_ratio >= _MIN_TEST_RATIO,
        }

        doc_ratio = comp.get("doc_files", 0) / source_files if source_files else 0.0
        generators = comp.get("doc_generators", [])
        sub["documentation_quality"] = {
            "label": "Documentation Quality Evaluation",
            "value": f"{comp.get('doc_files', 0):,} documentation files "
                     f"({doc_ratio:.2f} ratio)"
                     + (f"; {', '.join(generators)}" if generators else ""),
            "passing": bool(generators) or doc_ratio >= _MIN_DOC_RATIO,
        }

        share = refactor.get("share")
        sub["refactoring_tracking"] = {
            "label": "Refactoring and Evolution Tracking",
            "value": f"{refactor.get('refactor_commits', 0)} of "
                     f"{refactor.get('sampled', 0)} recent commits"
                     if refactor.get("sampled") else "No commits sampled",
            "detail": "; ".join(refactor.get("examples", [])) or None,
            "passing": share is not None and share >= _MIN_REFACTOR_SHARE,
        }

        score = sum(1 for s in sub.values() if s["passing"])
        return {
            "score": score,
            "max_score": len(sub),
            "percentage": round(score / len(sub) * 100, 2),
            "sub_scores": sub,
        }

    def _empty_result(self, repo_name: str) -> Dict[str, Any]:
        comp = {"source_files": 0, "test_files": 0, "doc_files": 0, "max_depth": 0,
                "mean_source_bytes": 0, "largest_source_bytes": 0,
                "large_file_share": 0.0, "doc_generators": [], "truncated": False,
                "languages": []}
        refactor = {"sampled": 0, "refactor_commits": 0, "share": None}
        return {
            "package_name": repo_name,
            "repository": "unknown",
            "timestamp": self._get_timestamp(),
            "composition": comp,
            "refactoring": refactor,
            "overall_score": self._calculate_score(comp, refactor),
        }
