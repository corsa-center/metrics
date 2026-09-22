"""
Software Supply Chain Integrity Collector (CASS Report Section 4.3.8)

Checks the transparency and verifiability of a project's build and
distribution pipeline:
  - SBOM Detection                : an SPDX/CycloneDX bill of materials,
                                     committed to the repository root or
                                     published as a release asset
  - Build Provenance              : a SLSA / in-toto attestation published
                                     alongside a release
  - Dependency Vulnerability Posture : known vulnerabilities in the
                                     project's own pinned dependencies, via
                                     OSV.dev's free batch query API -- no
                                     Dependabot alert access needed on the
                                     target repo, unlike GitHub's own
                                     vulnerability-alerts API. Reads six
                                     lockfile shapes, root-level only:
                                     requirements.txt and Pipfile.lock
                                     (PyPI, exact == pins), uv.lock and
                                     poetry.lock (PyPI, registry-sourced
                                     packages), Cargo.lock (crates.io,
                                     registry+ sourced packages), and go.sum
                                     (Go -- the module system IS the
                                     registry, so every entry qualifies).
                                     package-lock.json / yarn.lock aren't
                                     parsed yet. See _LOCKFILE_SPECS.

Dependency Freshness (libyears) is not collected here: it needs a
machine-readable dependency manifest with enough history to compute a lag,
which is a separate, larger undertaking from checking pinned versions
against known vulnerabilities. Per CASS §3.5, it's reported as
not-yet-collected rather than scored zero.

Badge and Scorecard level are not fetched here either — they're already
collected by openssf_badge.py / openssf_scorecard.py (Section 4.2) and are
surfaced alongside this section by the dashboard transform rather than
re-fetched.
"""

import asyncio
import base64
import httpx
import json
import logging
import re
import tomllib
from typing import Any, Dict, List, Optional, Tuple

from collectors.ecosystem.base import COLLECTION_GAP, GitHubCollectorBase, RepoTree, RetryingTransport

logger = logging.getLogger(__name__)

_SBOM_ROOT_FILES = [
    "sbom.spdx.json", "SBOM.spdx.json", "sbom.json", "SBOM.json",
    "bom.json", "bom.xml", ".github/sbom.spdx.json",
]

# Filename fragments matched against release-asset names (lower-cased).
_SBOM_ASSET_HINTS = ["sbom", "spdx", "cyclonedx", "bom.xml", "bom.json", ".cdx.json"]
_PROVENANCE_ASSET_HINTS = [
    "intoto", "in-toto", "provenance", "slsa", ".sigstore", "attestation",
]

_RELEASES_SAMPLE = 5

# Root-level only, matching reproducibility.py's own "pip lock" candidates.
# requirements.txt shows up at 25 other, nested paths across the portfolio
# (docs/requirements.txt, .github/workflows/.../requirements.txt,
# tests/requirements.txt) that are Sphinx/CI/test tooling pins, not the
# project's own dependency surface -- deliberately not matched here, unlike
# the case-insensitive-but-otherwise-permissive RepoTree.match() used
# elsewhere, since flagging a stale Sphinx theme pin as a "supply chain"
# finding would be noise, not signal.
_REQUIREMENTS_TXT_PATHS = ["requirements.txt", "requirements/requirements.txt"]
_UV_LOCK_PATHS = ["uv.lock"]
_POETRY_LOCK_PATHS = ["poetry.lock"]
_CARGO_LOCK_PATHS = ["Cargo.lock"]
_GO_SUM_PATHS = ["go.sum"]
_PIPFILE_LOCK_PATHS = ["Pipfile.lock"]

# An exactly-pinned PyPI requirement line: name[extras]==version, with an
# optional environment marker or comment already stripped by the caller.
# OSV.dev's query API takes a single version, not a range, so anything
# short of == (>=, ~=, a bare name) names a real dependency this can't
# check -- silently skipped rather than guessed at.
_PYPI_PIN_RE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*==\s*([A-Za-z0-9][A-Za-z0-9.+!_-]*)$"
)

# A go.sum content-hash line: "module version h1:hash". Each real
# dependency also gets a second line for its bare go.mod file's hash
# (version suffixed "/go.mod"), which this deliberately excludes --
# requiring the version token to contain no "/" is what tells the two
# apart, since a plain regex on "v\S+" would swallow "/go.mod" too.
_GO_SUM_RE = re.compile(r"^(\S+)\s+(v[0-9][^\s/]*)\s+h1:")

_OSV_BATCH_URL = "https://api.osv.dev/v1/querybatch"


def _parse_pinned_pypi_deps(text: str) -> List[Tuple[str, str]]:
    """Exactly-pinned (name, version) pairs from a requirements.txt-shaped
    file. Lines using a range (>=, ~=, <), a bare name, a VCS/URL
    requirement, or a pip option (-e, -r, --index-url) are skipped --
    real dependencies, just not ones a single-version query can check.
    """
    deps = []
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].split(";", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        match = _PYPI_PIN_RE.match(line)
        if match:
            deps.append((match.group(1), match.group(2)))
    return deps


def _parse_uv_lock(text: str) -> List[Tuple[str, str]]:
    """(name, version) pairs for uv.lock packages sourced from a plain PyPI
    registry. uv.lock records `source = { registry = "..." }` for a normal
    published package and `{ path = ... }` / `{ git = ... }` / `{ editable =
    ... }` / `{ virtual = ... }` for anything else -- only the registry
    shape names a real, queryable release.
    """
    try:
        data = tomllib.loads(text)
    except Exception as e:
        logger.debug(f"Could not parse uv.lock: {e}")
        return []
    deps = []
    for pkg in data.get("package", []):
        name, version, source = pkg.get("name"), pkg.get("version"), pkg.get("source")
        if name and version and isinstance(source, dict) and "registry" in source:
            deps.append((name, version))
    return deps


def _parse_poetry_lock(text: str) -> List[Tuple[str, str]]:
    """(name, version) pairs for poetry.lock packages from a standard (or
    legacy custom-index) PyPI source. Unlike uv.lock, poetry.lock only
    records a [package.source] table at all for git/url/directory/file
    dependencies -- its *absence* is what marks a normal registry package,
    the reverse of uv.lock's convention.
    """
    try:
        data = tomllib.loads(text)
    except Exception as e:
        logger.debug(f"Could not parse poetry.lock: {e}")
        return []
    deps = []
    for pkg in data.get("package", []):
        name, version = pkg.get("name"), pkg.get("version")
        source_type = pkg.get("source", {}).get("type")
        if name and version and source_type not in {"git", "url", "directory", "file"}:
            deps.append((name, version))
    return deps


def _parse_cargo_lock(text: str) -> List[Tuple[str, str]]:
    """(name, version) pairs for Cargo.lock packages from the public
    crates.io registry. A path or git dependency (or one of the workspace's
    own crates) has no "registry+" source string -- either a different
    source form or no source field at all -- and is skipped.
    """
    try:
        data = tomllib.loads(text)
    except Exception as e:
        logger.debug(f"Could not parse Cargo.lock: {e}")
        return []
    deps = []
    for pkg in data.get("package", []):
        name, version, source = pkg.get("name"), pkg.get("version"), pkg.get("source")
        if name and version and isinstance(source, str) and source.startswith("registry+"):
            deps.append((name, version))
    return deps


def _parse_go_sum(text: str) -> List[Tuple[str, str]]:
    """(module path, version) pairs from a go.sum file's content-hash
    lines. The module path is used as-is as the OSV package name (Go's
    module system IS the registry -- there's no separate package name), and
    the version keeps its "v" prefix, since that's the literal tag Go
    itself resolved and the form OSV's Go ecosystem expects.
    """
    deps = []
    seen = set()
    for line in text.splitlines():
        match = _GO_SUM_RE.match(line.strip())
        if match:
            key = (match.group(1), match.group(2))
            if key not in seen:
                seen.add(key)
                deps.append(key)
    return deps


def _parse_pipfile_lock(text: str) -> List[Tuple[str, str]]:
    """(name, version) pairs from a Pipfile.lock's "default" and "develop"
    sections. Unlike the root-only rule for *where* a lockfile is allowed
    to live, a vulnerable entry inside one legitimate lockfile isn't
    excluded just for being a dev/test dependency -- it still runs
    somewhere, if only in CI, and a known vulnerability there is a real
    finding, not noise. Only exactly-pinned ("==...") entries are usable,
    same reasoning as requirements.txt.
    """
    try:
        data = json.loads(text)
    except Exception as e:
        logger.debug(f"Could not parse Pipfile.lock: {e}")
        return []
    deps = []
    for section in ("default", "develop"):
        for name, info in (data.get(section) or {}).items():
            version = info.get("version", "") if isinstance(info, dict) else ""
            if isinstance(version, str) and version.startswith("=="):
                deps.append((name, version[2:]))
    return deps


# (OSV ecosystem, root-level candidate paths, parser) for every lockfile
# shape this checks. All six happen to overlap with reproducibility.py's
# own "dependency_pinning" candidates, but each needs its own parser --
# requirements.txt and go.sum are line-oriented, Pipfile.lock is JSON, and
# the rest are TOML with three different conventions for telling a
# registry package apart from a git/path/url one.
_LOCKFILE_SPECS: List[Tuple[str, List[str], Any]] = [
    ("PyPI", _REQUIREMENTS_TXT_PATHS, _parse_pinned_pypi_deps),
    ("PyPI", _UV_LOCK_PATHS, _parse_uv_lock),
    ("PyPI", _POETRY_LOCK_PATHS, _parse_poetry_lock),
    ("PyPI", _PIPFILE_LOCK_PATHS, _parse_pipfile_lock),
    ("crates.io", _CARGO_LOCK_PATHS, _parse_cargo_lock),
    ("Go", _GO_SUM_PATHS, _parse_go_sum),
]


class SupplyChainCollector(GitHubCollectorBase):
    """Collects supply-chain transparency indicators (CASS Report Section 4.3.8)."""

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        repo_name = package.get("name", "Unknown")
        repo_url = package.get("repo_url", "")

        owner_repo = self._extract_owner_repo(repo_url)
        if not owner_repo:
            logger.error(f"Could not extract owner/repo from {repo_url}")
            return self._empty_result(repo_name)

        owner, repo = owner_repo
        logger.info(f"Collecting supply chain metrics for {owner}/{repo}")

        async with httpx.AsyncClient(timeout=30.0, transport=RetryingTransport()) as client:
            (release_assets, assets_gap), tree = await asyncio.gather(
                self._fetch_release_assets(client, owner, repo),
                RepoTree.fetch(client, self.github_headers, owner, repo),
            )
            root_sbom, root_gap = self._check_root_sbom(tree)
            dep_vulns = await self._check_dependency_vulnerabilities(client, owner, repo, tree)

        sbom = self._find_sbom(root_sbom, release_assets, root_gap or assets_gap)
        provenance = self._find_provenance(release_assets, assets_gap)

        sub_metrics = {
            "sbom_detection": sbom,
            "build_provenance": provenance,
            "dependency_vulnerability_posture": dep_vulns,
            "dependency_freshness": {
                "label": "Dependency Freshness",
                "value": None, "passing": False, "not_collected": True,
            },
        }

        overall = self._compute_overall(sub_metrics)

        return {
            "package_name": repo_name,
            "repository": f"{owner}/{repo}",
            "timestamp": self._get_timestamp(),
            "has_sbom": sbom["passing"],
            "has_build_provenance": provenance["passing"],
            "sub_metrics": sub_metrics,
            "overall_score": overall,
        }

    # ------------------------------------------------------------------ #
    # Fetching                                                             #
    # ------------------------------------------------------------------ #

    def _check_root_sbom(self, tree) -> tuple:
        """Returns (html_url_or_None, saw_gap). Resolved against a RepoTree
        (case-insensitive, single fetch) rather than probed one literal path
        at a time -- see METRIC_BLIND_SPOTS.md class F1.
        """
        if tree is COLLECTION_GAP:
            return None, True
        return tree.match_url(_SBOM_ROOT_FILES), False

    async def _fetch_release_assets(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> tuple:
        """Returns (flat list of {name, url, release}, is_gap). An empty
        list with is_gap=False is a real, confirmed result (no releases);
        is_gap=True means the release list itself couldn't be fetched, so
        an empty list here says nothing trustworthy about SBOM/provenance
        presence.
        """
        releases = await self._github_get(
            client, f"https://api.github.com/repos/{owner}/{repo}/releases",
            params={"per_page": _RELEASES_SAMPLE},
        )
        if releases is COLLECTION_GAP:
            return [], True

        assets = []
        for release in releases or []:
            for asset in release.get("assets", []) or []:
                name = asset.get("name")
                if name:
                    assets.append({
                        "name": name,
                        "url": asset.get("browser_download_url", ""),
                        "release": release.get("tag_name", ""),
                    })
        return assets, False

    async def _check_dependency_vulnerabilities(
        self, client: httpx.AsyncClient, owner: str, repo: str, tree
    ) -> Dict[str, Any]:
        """Dependency Vulnerability Posture: known vulnerabilities in the
        project's own pinned dependencies, via OSV.dev's free, unauthenticated
        batch query API -- no Dependabot alert access needed on the target
        repo, unlike GitHub's own vulnerability-alerts API.

        Every lockfile shape in _LOCKFILE_SPECS present at the repository
        root is read and merged into one query; a repo can have more than
        one (Python bindings pinned via requirements.txt alongside a Rust
        component's Cargo.lock, say). A gap fetching one file doesn't lose
        the ones that were read successfully -- same "a positive finding
        stands regardless of gaps elsewhere" convention as everywhere else
        in this codebase -- but if nothing was found across the files that
        DID come back clean, and something else gapped, that's reported as
        not_collected rather than a confident "no vulnerabilities".
        """
        if tree is COLLECTION_GAP:
            return {"label": "Dependency Vulnerability Posture", "value": None,
                    "passing": False, "not_collected": True}

        found_files: List[str] = []
        all_deps: List[Tuple[str, str, str]] = []
        saw_gap = False
        for ecosystem, paths, parser in _LOCKFILE_SPECS:
            path = tree.match(paths)
            if not path:
                continue
            data = await self._github_get(
                client, f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
            )
            if data is COLLECTION_GAP:
                saw_gap = True
                continue
            if data is None:
                continue
            text = base64.b64decode(data.get("content", "")).decode("utf-8", "replace")
            found_files.append(path)
            all_deps.extend((ecosystem, name, version) for name, version in parser(text))

        if not found_files:
            if saw_gap:
                return {"label": "Dependency Vulnerability Posture", "value": None,
                        "passing": False, "not_collected": True}
            return {
                "label": "Dependency Vulnerability Posture", "passing": True,
                "value": "No dependency lockfile found at the repository root",
            }

        if not all_deps:
            return {
                "label": "Dependency Vulnerability Posture", "passing": True,
                "value": f"{', '.join(found_files)} has no exactly-pinned/registry "
                         f"dependencies to check",
                "detail": found_files,
            }

        vulnerable = await self._query_osv_batch(client, all_deps)
        if vulnerable is COLLECTION_GAP:
            return {"label": "Dependency Vulnerability Posture", "value": None,
                    "passing": False, "not_collected": True}
        if not vulnerable and saw_gap:
            # Confirmed clean among what was read, but another lockfile's
            # content fetch failed -- it could be hiding the real answer.
            return {"label": "Dependency Vulnerability Posture", "value": None,
                    "passing": False, "not_collected": True}

        return {
            "label": "Dependency Vulnerability Posture",
            "passing": not vulnerable,
            "value": f"{len(vulnerable)} of {len(all_deps)} pinned dependencies "
                     f"have a known vulnerability",
            "detail": sorted(f"{name}=={version} ({ecosystem})" for ecosystem, name, version in vulnerable)
                      or None,
        }

    async def _query_osv_batch(
        self, client: httpx.AsyncClient, deps: List[Tuple[str, str, str]]
    ):
        """(ecosystem, name, version) triples with at least one known OSV
        vulnerability, or COLLECTION_GAP if the query itself failed. A dep
        absent from the result had a confirmed-clean scan, same as every
        other triple not returned. Severity isn't fetched -- OSV.dev's batch
        endpoint returns only vulnerability IDs, and a second round of
        per-ID lookups for detail isn't worth it for a boolean pass/fail
        sub-metric.
        """
        queries = [{"package": {"name": name, "ecosystem": ecosystem}, "version": version}
                   for ecosystem, name, version in deps]
        try:
            resp = await client.post(_OSV_BATCH_URL, json={"queries": queries})
        except Exception as e:
            logger.warning(f"COLLECTION-GAP url={_OSV_BATCH_URL} status=exception reason={e!r}")
            return COLLECTION_GAP
        if resp.status_code != 200:
            logger.warning(f"COLLECTION-GAP url={_OSV_BATCH_URL} status={resp.status_code}")
            return COLLECTION_GAP

        results = resp.json().get("results", [])
        return [deps[i] for i, r in enumerate(results) if r.get("vulns")]

    # ------------------------------------------------------------------ #
    # Matching                                                             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _find_sbom(
        root_url: Optional[str], assets: List[Dict[str, str]], has_gap: bool = False
    ) -> Dict[str, Any]:
        if root_url:
            return {
                "label": "SBOM Detection", "passing": True,
                "value": "Committed to repository", "detail": root_url,
            }
        for asset in assets:
            lower = asset["name"].lower()
            if any(hint in lower for hint in _SBOM_ASSET_HINTS):
                return {
                    "label": "SBOM Detection", "passing": True,
                    "value": f'Published with release {asset["release"]}: {asset["name"]}',
                    "detail": asset["url"],
                }
        if has_gap:
            return {"label": "SBOM Detection", "passing": False,
                     "value": None, "not_collected": True}
        return {"label": "SBOM Detection", "passing": False, "value": "No SBOM found"}

    @staticmethod
    def _find_provenance(assets: List[Dict[str, str]], has_gap: bool = False) -> Dict[str, Any]:
        for asset in assets:
            lower = asset["name"].lower()
            if any(hint in lower for hint in _PROVENANCE_ASSET_HINTS):
                return {
                    "label": "Build Provenance", "passing": True,
                    "value": f'Published with release {asset["release"]}: {asset["name"]}',
                    "detail": asset["url"],
                }
        if has_gap:
            return {"label": "Build Provenance", "passing": False,
                     "value": None, "not_collected": True}
        return {
            "label": "Build Provenance", "passing": False,
            "value": "No build provenance found",
        }

    # ------------------------------------------------------------------ #
    # Scoring                                                              #
    # ------------------------------------------------------------------ #

    def _compute_overall(self, sub_metrics: Dict[str, Any]) -> Dict[str, Any]:
        """Score only what was actually measured.

        Mirrors the convention in collaboration.py / orchestrator.yaml:
        an indicator that can't be automated is dropped from the average
        rather than counted as a failure (CASS §3.5).
        """
        scorable = {k: v for k, v in sub_metrics.items() if not v.get("not_collected")}
        score = sum(1 for v in scorable.values() if v.get("passing"))
        max_score = len(scorable)
        return {
            "score": score,
            "max_score": max_score,
            "percentage": round(score / max_score * 100, 2) if max_score else 0.0,
        }

    def _empty_result(self, repo_name: str) -> Dict[str, Any]:
        sub_metrics = {
            "sbom_detection": {"label": "SBOM Detection", "value": None, "passing": False},
            "build_provenance": {"label": "Build Provenance", "value": None, "passing": False},
            "dependency_vulnerability_posture": {
                "label": "Dependency Vulnerability Posture",
                "value": None, "passing": False, "not_collected": True,
            },
            "dependency_freshness": {
                "label": "Dependency Freshness",
                "value": None, "passing": False, "not_collected": True,
            },
        }
        return {
            "package_name": repo_name,
            "repository": "unknown",
            "timestamp": self._get_timestamp(),
            "has_sbom": False,
            "has_build_provenance": False,
            "sub_metrics": sub_metrics,
            "overall_score": {"score": 0, "max_score": 2, "percentage": 0.0},
        }
