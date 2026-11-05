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
from typing import Any, Dict, List, Optional

import httpx

from forge.base import COLLECTION_GAP, RetryingTransport
from forge.interface import Forge
from collectors.ecosystem.base import _VENDORED_DIR, get_threshold

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

# Includes the unit_test/ and *_UnitTests.cpp conventions common in C++ HPC
# code, and HDF5's testpar/ (its parallel test suite).
_TEST_PATH = re.compile(r"(^|/)(tests?|testing|testpar|unit_?tests?)(/|$)", re.IGNORECASE)
_TEST_FILE = re.compile(
    r"(^|/)(test_[^/]+|[^/]+_(?:unit)?tests?|[^/]*UnitTests?[^/]*)\.[a-z0-9]+$", re.IGNORECASE)
_DOC_PATH = re.compile(r"(^|/)(docs?|documentation)(/|$)", re.IGNORECASE)

# Configuration for a documentation generator — the difference between having
# prose lying around and publishing real API documentation.
_DOC_GENERATORS = {
    "Doxygen": re.compile(r"(^|/)Doxyfile(\.[^/]+)?$", re.IGNORECASE),
    "Sphinx": re.compile(r"(^|/)(docs?|documentation)/(source/)?conf\.py$", re.IGNORECASE),
    "MkDocs": re.compile(r"(^|/)mkdocs\.ya?ml$", re.IGNORECASE),
    "Javadoc/Gradle": re.compile(r"(^|/)javadoc\.gradle$", re.IGNORECASE),
}
# What _DOC_GENERATORS matches, named the way a reader would look for it.
_DOC_GENERATOR_FILES = ["Doxyfile", "docs/conf.py", "docs/source/conf.py", "mkdocs.yml",
                        "javadoc.gradle"]

# Commit subjects that indicate deliberate structural work. "remove" alone is
# deliberately absent — it matches bug fixes like "Remove abort on infinite
# loop", which is not refactoring.
_REFACTOR_INTENT = re.compile(
    r"\b(refactor\w*|cleanup|clean[- ]up|simplif\w+|deprecat\w+|restructur\w+"
    r"|rework\w*|moderni[sz]\w+|tidy|dead code"
    r"|remove (?:unused|dead|obsolete|redundant|noop|no-op|legacy|stale))\b",
    re.IGNORECASE,
)
# _REFACTOR_INTENT's vocabulary as the report lists it, for a team wondering
# which commit subjects count.
_REFACTOR_WORDS = [
    "refactor…", "cleanup", "clean up", "simplif…", "deprecat…", "restructur…",
    "rework…", "moderniz…", "tidy", "dead code",
    "remove unused / dead / obsolete / redundant / noop / no-op / legacy / stale",
]

# Sampled across three pages. At 100 commits each commit is worth a full
# percentage point, which makes a 3% threshold indistinguishable from noise.
_COMMIT_SAMPLE = 100
_COMMIT_PAGES = 3

# Source files named under the complexity row; past a handful the list stops
# pointing at what to split.
_LARGE_FILES_SHOWN = 5


def _test_dir(path: str) -> str:
    """The test directory a test file sits in or, for a file named as a test
    outside one, its own directory ("" at the root)."""
    m = _TEST_PATH.search(path)
    if m:
        return path[:m.end(2)]
    return path.rsplit("/", 1)[0] if "/" in path else ""


class MaintainabilityCollector:
    """Collects maintainability and understandability signals (Section 4.3.6)."""

    def __init__(self, forge: Forge):
        self.forge = forge

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        repo_name = package.get("name", "Unknown")
        ref = self.forge.extract_ref(package.get("repo_url", ""))
        if not ref:
            logger.error(f"Could not extract a repo reference from {package.get('repo_url')}")
            return self._empty_result(repo_name)

        logger.info(f"Collecting maintainability metrics for {repo_name}")

        async with httpx.AsyncClient(timeout=60.0, transport=RetryingTransport()) as client:
            tree, languages, refactor = await asyncio.gather(
                self._get_tree(client, ref),
                self._get_languages(client, ref),
                self._get_refactor_activity(client, ref),
                return_exceptions=True,
            )

        if isinstance(tree, Exception):
            logger.warning(f"Tree fetch failed: {tree}")
            tree = {"files": [], "truncated": False, "gap": True}
        if isinstance(languages, Exception):
            logger.warning(f"Language fetch failed: {languages}")
            languages = {}
        if isinstance(refactor, Exception):
            logger.warning(f"Commit scan failed: {refactor}")
            refactor = {"sampled": 0, "refactor_commits": 0, "share": None, "gap": True}

        composition = self._analyze_tree(tree["files"])
        composition["truncated"] = tree["truncated"]
        if tree.get("gap"):
            composition["gap"] = True
        composition["languages"] = sorted(languages, key=languages.get, reverse=True)[:5]

        return {
            "package_name": repo_name,
            "repository": ref,
            "timestamp": self.forge.get_timestamp(),
            "composition": composition,
            "refactoring": refactor,
            "overall_score": self._calculate_score(composition, refactor, ref),
        }

    # ------------------------------------------------------------------ fetch

    async def _get_tree(
        self, client: httpx.AsyncClient, ref: str
    ) -> Dict[str, Any]:
        data = await self.forge.repo_tree(client, ref)
        if data is COLLECTION_GAP:
            return {"files": [], "truncated": False, "gap": True}
        if not data:
            return {"files": [], "truncated": False}
        return data

    async def _get_languages(
        self, client: httpx.AsyncClient, ref: str
    ) -> Dict[str, int]:
        data = await self.forge.languages(client, ref)
        return data if data else {}

    async def _get_refactor_activity(
        self, client: httpx.AsyncClient, ref: str
    ) -> Dict[str, Any]:
        """Refactoring intent across the most recent commits.

        Commit subjects are used rather than the code-frequency statistics API,
        which returns HTTP 422 for any repository over 10,000 commits — that
        rules out most of this portfolio (HDF5 alone has ~24,500).
        """
        subjects: List[str] = []
        gap = False
        for page in range(1, _COMMIT_PAGES + 1):
            batch = await self.forge.commits(client, ref, per_page=_COMMIT_SAMPLE, page=page)
            if not batch:
                gap = batch is COLLECTION_GAP
                break
            subjects.extend(c["subject"] for c in batch)
        if not subjects:
            # A later page failing still leaves a usable, smaller sample; only
            # a failure with nothing read is a gap rather than "no commits".
            return {"sampled": 0, "refactor_commits": 0, "share": None,
                    **({"gap": True} if gap else {})}

        matches: List[str] = []
        terms: Dict[str, int] = {}
        for subject in subjects:
            m = _REFACTOR_INTENT.search(subject)
            if m:
                matches.append(subject)
                term = re.sub(r"\s+", " ", m.group(1).lower())
                terms[term] = terms.get(term, 0) + 1
        return {
            "sampled": len(subjects),
            "refactor_commits": len(matches),
            "share": round(len(matches) / len(subjects), 3),
            "examples": matches[:3],
            # Most common first, so the report shows which words did count.
            "matched_terms": dict(sorted(terms.items(), key=lambda t: (-t[1], t[0]))),
        }

    # ---------------------------------------------------------------- analyze

    def _analyze_tree(self, files: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Classify the tree into source, test and documentation files."""
        source, tests, docs = [], [], []
        max_depth, deepest = 0, None
        generators: Dict[str, str] = {}  # name -> first config file found
        test_dirs: Dict[str, int] = {}

        for f in files:
            path = f["path"]
            # Bundled third-party code (Kokkos's tpls/googletest) is neither
            # the project's source, its tests nor its documentation.
            if _VENDORED_DIR.search(path):
                continue

            for name, pattern in _DOC_GENERATORS.items():
                if name not in generators and pattern.search(path):
                    generators[name] = path

            ext = ("." + path.rsplit(".", 1)[-1].lower()) if "." in path else ""
            is_test = bool(_TEST_PATH.search(path) or _TEST_FILE.search(path))

            if ext in _SOURCE_EXTENSIONS:
                # A test file is not also counted as source, so the ratio below
                # compares tests against the code they actually cover.
                (tests if is_test else source).append(f)
                if is_test:
                    d = _test_dir(path)
                    test_dirs[d] = test_dirs.get(d, 0) + 1
                # Depth of the code itself: test data fixtures (recorded
                # mirror layouts, sample meshes) nest deeper than anything a
                # maintainer navigates, and one such file set the whole value.
                elif path.count("/") + 1 > max_depth:
                    max_depth, deepest = path.count("/") + 1, path
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

        large_file_bytes = get_threshold("4.3.6", "Advanced Complexity Analysis", "large_file_bytes")
        sizes = [f["size"] for f in source]
        large = [s for s in sizes if s > large_file_bytes]
        largest = sorted(source, key=lambda f: (-f["size"], f["path"]))[:_LARGE_FILES_SHOWN]
        return {
            "source_files": len(source),
            "test_files": len(tests),
            "doc_files": len(docs),
            "max_depth": max_depth,
            "mean_source_bytes": round(sum(sizes) / len(sizes)) if sizes else 0,
            "largest_source_bytes": max(sizes, default=0),
            "large_file_share": round(len(large) / len(sizes), 3) if sizes else 0.0,
            "doc_generators": sorted(generators),
            # The files behind the figures above, for the report's evidence.
            "files_scanned": len(files),
            "largest_sources": [{"path": f["path"], "size": f["size"]} for f in largest],
            "deepest_source": deepest,
            "doc_generator_files": dict(sorted(generators.items())),
            "test_dirs": dict(sorted(test_dirs.items(), key=lambda t: (-t[1], t[0]))[:5]),
        }

    def _url(self, ref: Optional[str], path: str, kind: str = "blob") -> Optional[str]:
        return self.forge.web_url(ref, path, kind) if self.forge and ref and path else None

    def _calculate_score(
        self, comp: Dict, refactor: Dict, ref: Optional[str] = None
    ) -> Dict[str, Any]:
        sub: Dict[str, Dict[str, Any]] = {}
        approx = " (approximate — tree truncated)" if comp.get("truncated") else ""

        source_files = comp.get("source_files", 0)
        large_share = comp.get("large_file_share", 0.0)
        depth = comp.get("max_depth", 0)
        manageable = (
            source_files > 0
            and large_share <= get_threshold("4.3.6", "Advanced Complexity Analysis", "max_large_file_share")
            and depth <= get_threshold("4.3.6", "Advanced Complexity Analysis", "max_tree_depth")
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
            "passing": test_ratio >= get_threshold("4.3.6", "Code Quality Assessment"),
        }

        doc_ratio = comp.get("doc_files", 0) / source_files if source_files else 0.0
        generators = comp.get("doc_generators", [])
        sub["documentation_quality"] = {
            "label": "Documentation Quality Evaluation",
            "value": f"{comp.get('doc_files', 0):,} documentation files "
                     f"({doc_ratio:.2f} ratio)"
                     + (f"; {', '.join(generators)}" if generators else ""),
            "passing": bool(generators) or doc_ratio >= get_threshold("4.3.6", "Documentation Quality Evaluation"),
        }

        share = refactor.get("share")
        sub["refactoring_tracking"] = {
            "label": "Refactoring and Evolution Tracking",
            "value": f"{refactor.get('refactor_commits', 0)} of "
                     f"{refactor.get('sampled', 0)} recent commits"
                     if refactor.get("sampled") else "No commits sampled",
            "detail": "; ".join(refactor.get("examples", [])) or None,
            "passing": share is not None and share >= get_threshold("4.3.6", "Refactoring and Evolution Tracking"),
        }

        # An empty tree is as likely a failed fetch as an empty repository,
        # so nothing is claimed about where the collector looked.
        if comp.get("files_scanned"):
            for key, evidence in self._tree_evidence(comp, ref).items():
                sub[key]["evidence"] = evidence
        if refactor.get("sampled"):
            sub["refactoring_tracking"]["evidence"] = self._refactor_evidence(refactor)

        # A failed fetch reads the same as an empty repository above; a row
        # it left short is not a measured fail.
        gapped = []
        if comp.get("gap"):
            gapped += ["complexity_analysis", "code_quality", "documentation_quality"]
        if refactor.get("gap"):
            gapped.append("refactoring_tracking")
        for key in gapped:
            if not sub[key]["passing"]:
                sub[key]["not_collected"] = True
        scored = [s for s in sub.values() if not s.get("not_collected")]
        score = sum(1 for s in scored if s["passing"])
        if not scored:
            return {"score": None, "max_score": 0, "percentage": None,
                    "status": "not_collected", "sub_scores": sub}
        return {
            "score": score,
            "max_score": len(scored),
            "percentage": round(score / len(scored) * 100, 2),
            "sub_scores": sub,
        }

    def _tree_evidence(self, comp: Dict, ref: Optional[str]) -> Dict[str, Dict]:
        """For the report: which files the tree-based rows rest on, and what
        each looked for and didn't find."""
        large_file_bytes = get_threshold("4.3.6", "Advanced Complexity Analysis", "large_file_bytes")
        largest = comp.get("largest_sources", [])
        # The files over the size limit are the ones to split; with none, the
        # largest shows how much room is left.
        over = [f for f in largest if f["size"] > large_file_bytes]
        shown = over or largest[:1]
        complexity: Dict[str, List] = {"found": [
            {"text": (f"{f['size'] / 1024:.1f} KB source file" if over
                      else f"largest source file ({f['size'] / 1024:.1f} KB)"),
             "path": f["path"], "url": self._url(ref, f["path"])}
            for f in shown
        ]}
        deepest = comp.get("deepest_source")
        if deepest:
            complexity["found"].append({
                "text": f"deepest source file (depth {comp.get('max_depth', 0)})",
                "path": deepest, "url": self._url(ref, deepest)})
        if not comp.get("source_files"):
            complexity["missing"] = [{"text": "source files (extensions "
                                      + ", ".join(sorted(_SOURCE_EXTENSIONS)) + ")"}]

        quality: Dict[str, List] = {"found": [
            {"text": f"{n:,} test file{'s' if n != 1 else ''}",
             "path": d or "the repository root", "url": self._url(ref, d, "tree")}
            for d, n in comp.get("test_dirs", {}).items()
        ]}
        if not comp.get("test_files"):
            quality["missing"] = [{"text": "test files: source files under a test, tests, "
                                           "testing or unit_test(s) directory, or named "
                                           "test_*, *_test(s) or *UnitTest(s)*"}]

        docs: Dict[str, List] = {
            "found": [{"text": f"{name} configuration", "path": path, "url": self._url(ref, path)}
                      for name, path in comp.get("doc_generator_files", {}).items()],
            "missing": [],
        }
        if not docs["found"]:
            docs["missing"].append({"text": "documentation generator configuration ("
                                            + ", ".join(_DOC_GENERATOR_FILES) + ")"})
        if not comp.get("doc_files"):
            docs["missing"].append({"text": "documentation files: "
                                            + ", ".join(sorted(_DOC_EXTENSIONS))
                                            + " outside test directories, or .txt at the root "
                                              "or under a doc, docs or documentation directory"})
        return {"complexity_analysis": complexity, "code_quality": quality,
                "documentation_quality": docs}

    @staticmethod
    def _refactor_evidence(refactor: Dict) -> Dict[str, List]:
        """For the report: the words that matched in commit subjects, and
        the ones that would have."""
        return {
            "found": [{"text": f"“{term}” in {n} commit subject{'s' if n != 1 else ''}"}
                      for term, n in refactor.get("matched_terms", {}).items()],
            "searched": [{
                "text": f"Words looked for in the {refactor['sampled']} most recent commit subjects",
                "paths": [{"path": w} for w in _REFACTOR_WORDS],
            }],
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
            "timestamp": self.forge.get_timestamp(),
            "composition": comp,
            "refactoring": refactor,
            "overall_score": self._calculate_score(comp, refactor),
        }
