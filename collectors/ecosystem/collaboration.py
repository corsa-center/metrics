"""
Collaboration Collector (CASS Report Section 4.2.7)

Measures how far a project reaches into the wider scientific-software ecosystem,
using the free, unauthenticated ecosyste.ms APIs.

Two sub-metrics are answerable:

  - Advanced Dependency Analysis   : which package ecosystems ship the software
  - Collaboration Network Analysis : how much downstream software depends on it

Packages are found by *repository URL* rather than by guessing a package name,
which avoids the classic mistake of assuming the GitHub repo name is the package
name (HDF5's PyPI presence is `h5py`, a different project entirely).

Spack is additionally looked up by name, because Spack package recipes usually
record the project's own homepage as their repository URL rather than the GitHub
repo — HDF5's Spack entry points at support.hdfgroup.org, so the repository-URL
lookup alone misses the single most relevant package manager for this portfolio.

conda-forge is looked up by name on anaconda.org too, because ecosyste.ms does
not index every feedstock: AMReX ships `amrex` on conda-forge but ecosyste.ms
has no record of it. A name match is only accepted when the package's own
metadata (dev_url / home / source_git_url) points back at this repository, so
an unrelated package that happens to share the name is not credited.

The collector also produces the Installation Success Tracking figure that CASS
section 4.3.4 needs, since it rests on the same registry data.

Not collected: Cross-project Reference Detection (the report specifies AI
analysis of issues and PRs), Interoperability Assessment and Standards
Compliance Tracking (both need domain-specific standards knowledge).
"""

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx

from forge.interface import Forge
from collectors.common import get_threshold

logger = logging.getLogger(__name__)

_PACKAGES_API = "https://packages.ecosyste.ms/api/v1"
_ANACONDA_API = "https://api.anaconda.org/package/conda-forge"
_SPACK_PACKAGES = "https://packages.spack.io/data/packages"
# Every Spack recipe with its homepages and download URLs, for finding the
# recipe that builds a repository when it goes by another name than the
# repository. Fetched once per run.
_SPACK_INDEX = "https://packages.spack.io/data/repology.json"
_GITHUB_REPO_URL = re.compile(r"github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?(?=[/#?]|$)", re.I)
_spack_by_repo: Optional[Dict[str, List[str]]] = None
_spack_index_lock = asyncio.Lock()

# Source-level dependents found by the dependency-audit tool
# (corsa-center/dependent-audit), published per project. Registries miss
# code consumed from source -- a git submodule, a CMake FetchContent, a
# header include -- which is how most HPC libraries are used.
_DEPENDENCY_AUDIT_PATH = ("corsa-center/project-dependent-tracking/"
                          "{name}-dependency-context/dependency_graph.json")
_DEPENDENCY_AUDIT = ("https://raw.githubusercontent.com/corsa-center/project-dependent-tracking"
                     "/main/{name}-dependency-context/dependency_graph.json")
_AUDIT_CONFIDENCE = {"high", "medium"}

# ecosyste.ms publishes a low per-second rate limit; one retry with a pause
# covers the throttling seen when several lookups run back to back.
_RATE_LIMIT_PAUSE_SECONDS = 2


class _FetchFailed:
    """_get_json's answer when a request failed rather than came back 404.

    Callers that only check isinstance() treat it like None; the lookups
    use it so the report never says a registry was searched when the
    search didn't get an answer.
    """

    def __bool__(self) -> bool:
        return False


_FETCH_FAILED = _FetchFailed()


class CollaborationCollector:
    """Collects ecosystem integration metrics (Section 4.2.7)."""

    def __init__(self, forge: Forge):
        self.forge = forge

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        repo_name = package.get("name", "Unknown")
        repo_url = package.get("repo_url", "")
        ref = self.forge.extract_ref(repo_url)
        if not ref:
            logger.error(f"Could not extract a repo reference from {repo_url}")
            return self._empty_result(repo_name)

        logger.info(f"Collecting collaboration metrics for {repo_name}")
        # Package registries key projects by "owner/name"; on GitLab the
        # owner may be a nested group path, so split at the last slash.
        owner, _, repo = ref.rpartition("/")

        # What each lookup searched, for the report's evidence lines.
        lookups: Dict[str, Dict[str, Any]] = {"repository": {}, "spack": {}, "conda": {}, "audit": {}}
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            # When the orchestrator ran Spack, it has already matched this
            # repository to its recipe (or found it has none); otherwise the
            # recipe is looked for by name.
            if "spack_match" in package:
                spack_lookup = self._spack_from_match(
                    client, ref, package["spack_match"], lookups["spack"])
            else:
                spack_lookup = self._lookup_spack(client, repo, owner, lookups["spack"])

            by_repo, spack, conda, source = await asyncio.gather(
                self._lookup_by_repository(client, ref, lookups["repository"]),
                spack_lookup,
                self._lookup_conda_forge(client, owner, repo, lookups["conda"]),
                self._source_dependents(client, owner, repo, lookups["audit"]),
                return_exceptions=True,
            )
        if isinstance(source, Exception):
            logger.warning(f"Dependency-audit lookup failed: {source}")
            source = None
            lookups["audit"]["outcome"] = "failed"

        if isinstance(by_repo, Exception):
            logger.warning(f"ecosyste.ms repository lookup failed: {by_repo}")
            by_repo = []
            lookups["repository"]["failed"] = True
        if isinstance(spack, Exception):
            logger.warning(f"Spack lookup failed: {spack}")
            spack = None
            lookups["spack"]["failed"] = True
        if isinstance(conda, Exception):
            logger.warning(f"conda-forge lookup failed: {conda}")
            conda = None
            lookups["conda"]["failed"] = True

        registries = self._merge(by_repo + [r for r in (spack, conda) if r])
        registries = self._drop_spurious_go_entries(registries, package.get("language"))
        return {
            "package_name": repo_name,
            "repository": ref,
            "timestamp": self.forge.get_timestamp(),
            "registries": registries,
            "ecosystems": sorted({r["ecosystem"] for r in registries}),
            "source_dependents": source,
            "overall_score": self._calculate_score(registries, source, lookups),
            # Distribution evidence, not part of the weighted score above —
            # see CASS §4.1.1 Considerations: download counts measure
            # distribution rather than use and are most informative as
            # trends, not absolute figures. `period` differs by registry
            # ("total" for conda, "last-month" for PyPI) so counts are not
            # directly comparable across ecosystems.
            "package_downloads": self._downloads_summary(registries),
            # 4.1.1 (Impact) shows the largest dependent counts from this
            # data; these name the package each came from. Matched by
            # section, since this is an Ecosystem collector.
            "row_evidence": {"reverse_dependencies": {
                "label": "Reverse-Dependency Analysis", "section": "impact/citation_and_adoption",
                "evidence": self._reverse_dependency_evidence(registries),
            }},
        }

    # ------------------------------------------------------------------ fetch

    async def _get_json(self, client: httpx.AsyncClient, url: str) -> Optional[Any]:
        """GET a JSON document, retrying once if the API throttles. None
        means 404; _FETCH_FAILED, that no answer came back."""
        for attempt in range(2):
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 404:
                    return None
                if attempt == 0 and resp.status_code in (429, 500, 502, 503):
                    logger.debug(f"HTTP {resp.status_code} from {url}, retrying…")
                    await asyncio.sleep(_RATE_LIMIT_PAUSE_SECONDS)
                    continue
                return _FETCH_FAILED
            except Exception as e:
                if attempt == 0:
                    logger.debug(f"Error fetching {url}: {e}, retrying…")
                    await asyncio.sleep(1)
                    continue
                return _FETCH_FAILED
        return _FETCH_FAILED

    async def _lookup_by_repository(
        self, client: httpx.AsyncClient, ref: str, trace: Optional[Dict] = None,
    ) -> List[Dict[str, Any]]:
        """Every package ecosyste.ms links back to this repository.

        `trace`, if given, records the repository URL searched for and
        whether the search failed, for the report (see _registry_evidence).
        """
        repository_url = f"https://{self.forge.host}/{ref}"
        data = await self._get_json(
            client, f"{_PACKAGES_API}/packages/lookup?repository_url={quote(repository_url, safe='')}"
        )
        if trace is not None:
            trace.update(url=repository_url, failed=data is _FETCH_FAILED)
        if not isinstance(data, list):
            return []
        return [self._normalize(p) for p in data if p.get("ecosystem") and p.get("name")]

    async def _spack_from_match(
        self, client: httpx.AsyncClient, ref: str, match: Optional[Dict[str, Any]], trace: Dict,
    ) -> Optional[Dict[str, Any]]:
        """The Spack package the orchestrator matched to this repository by
        running Spack itself (tools/spack_dependencies.py): by the URLs its
        recipe fetches from, searching builtin and the catalog's spack_repo
        repositories. None when Spack has none -- then nothing is looked up.

        ecosyste.ms supplies the registry record when it knows the package;
        a recipe outside builtin (SCOREC's pcms) it doesn't, so the record
        is built from what Spack reported.
        """
        trace["matched_by_spack"] = True
        trace["url"] = f"https://{self.forge.host}/{ref}"
        trace["names"] = []
        trace["failed"] = False
        if match is None:
            return None
        name = match["name"]
        trace["names"].append(name)
        data = await self._get_json(client, f"{_PACKAGES_API}/registries/spack.io/packages/{quote(name)}")
        if isinstance(data, dict) and data.get("name"):
            record = self._normalize(data)
        else:
            record = self._normalize({"ecosystem": "spack", "name": name,
                                      "install_command": f"spack install {name}"})
        # ecosyste.ms's Spack dependents lag Spack's own (AMReX 2 vs 6), so
        # take the larger, as _lookup_spack does.
        spack_count = match.get("dependent_packages") or 0
        record["dependent_packages"] = max(record["dependent_packages"], spack_count)
        return record

    async def _lookup_spack(
        self, client: httpx.AsyncClient, repo: str, owner: Optional[str] = None,
        trace: Optional[Dict] = None,
    ) -> Optional[Dict[str, Any]]:
        """Spack recipe for this project, looked up by name.

        Tried lower-cased as well, since Spack package names are lower-case by
        convention while repository names often are not (ADIOS2 -> adios2),
        then under any name whose recipe downloads from this repository.

        `trace`, if given, records the names tried, whether recipes were
        also matched by download URL, and whether any lookup failed.
        """
        trace = {} if trace is None else trace
        names = [repo, repo.lower()]
        if owner:
            names += await self._main_spack_recipe(client, owner, repo)
            # The index only knows GitHub URLs (see _spack_names_for).
            trace["by_download_url"] = (_spack_by_repo is not None
                                        and self.forge.host == "github.com")
        trace.update(names=[], failed=False)
        for name in dict.fromkeys(names):
            data = await self._get_json(
                client, f"{_PACKAGES_API}/registries/spack.io/packages/{quote(name)}"
            )
            trace["names"].append(name)
            trace["failed"] |= data is _FETCH_FAILED
            if isinstance(data, dict) and data.get("name"):
                record = self._normalize(data)
                # ecosyste.ms's Spack dependents lag Spack's own index
                # (AMReX 2 vs 6, HDF5 161 vs 222); take the larger.
                own = await self._get_json(client, f"{_SPACK_PACKAGES}/{quote(record['name'])}.json")
                if isinstance(own, dict) and isinstance(own.get("dependent_to"), list):
                    record["dependent_packages"] = max(
                        record["dependent_packages"], len(own["dependent_to"])
                    )
                return record
        return None

    async def _spack_names_for(self, client: httpx.AsyncClient, owner: str, repo: str) -> List[str]:
        """Spack packages whose homepage or download URLs are this
        repository. The index only recognizes GitHub URLs, so a GitLab
        project falls back to the name-based lookup."""
        global _spack_by_repo
        async with _spack_index_lock:
            if _spack_by_repo is None:
                data = await self._get_json(client, _SPACK_INDEX)
                if not isinstance(data, dict):
                    return []  # retried by the next package
                _spack_by_repo = self._index_spack_by_repository(data.get("packages", {}))
        return sorted(_spack_by_repo.get(f"{owner}/{repo}".lower(), []))

    async def _main_spack_recipe(self, client: httpx.AsyncClient, owner: str, repo: str) -> List[str]:
        """Of the recipes built from this repository (a monorepo can feed
        several), the one most packages depend on."""
        names = (await self._spack_names_for(client, owner, repo))[:5]
        if len(names) < 2:
            return names
        counts = []
        for name in names:
            own = await self._get_json(client, f"{_SPACK_PACKAGES}/{quote(name)}.json")
            deps = own.get("dependent_to") if isinstance(own, dict) else None
            counts.append(len(deps) if isinstance(deps, list) else 0)
        return [max(zip(counts, names), key=lambda cn: (cn[0], -names.index(cn[1])))[1]]

    @staticmethod
    def _index_spack_by_repository(packages: Dict[str, Any]) -> Dict[str, List[str]]:
        def strings(x):
            if isinstance(x, str):
                yield x
            elif isinstance(x, list):
                for y in x:
                    yield from strings(y)
            elif isinstance(x, dict):
                for y in x.values():
                    yield from strings(y)

        index: Dict[str, List[str]] = {}
        for name, pkg in packages.items():
            if not isinstance(pkg, dict):
                continue
            for text in strings([pkg.get("homepages"), pkg.get("downloads"), pkg.get("version")]):
                for m in _GITHUB_REPO_URL.finditer(text):
                    key = f"{m.group(1)}/{m.group(2)}".lower()
                    if name not in index.setdefault(key, []):
                        index[key].append(name)
        return index

    async def _lookup_conda_forge(
        self, client: httpx.AsyncClient, owner: str, repo: str, trace: Optional[Dict] = None,
    ) -> Optional[Dict[str, Any]]:
        """conda-forge package for this repository, looked up by name and
        accepted only if its metadata links back to the repository.

        `trace`, if given, records the names tried, packages found but not
        linked back (as {"name", "url"}), and whether any lookup failed.
        """
        trace = {} if trace is None else trace
        trace.update(names=[], unlinked=[], failed=False)
        target = f"{self.forge.host}/{owner}/{repo}".lower()
        for name in dict.fromkeys([repo.lower(), repo]):
            data = await self._get_json(client, f"{_ANACONDA_API}/{quote(name)}")
            trace["names"].append(name)
            trace["failed"] |= data is _FETCH_FAILED
            if not isinstance(data, dict) or not data.get("name"):
                continue
            links = [data.get(k) or "" for k in ("dev_url", "home", "source_git_url")]
            if not any(self._points_at(link, target) for link in links):
                logger.debug(f"conda-forge '{name}' does not link to {target}; not credited")
                # anaconda.org can answer both spellings with one package.
                if all(u["name"] != data["name"] for u in trace["unlinked"]):
                    trace["unlinked"].append({"name": data["name"], "url": data.get("html_url")})
                continue
            return {
                "ecosystem": "conda",
                "name": data["name"],
                # anaconda.org has no reverse-dependency count; _merge keeps
                # any higher figure ecosyste.ms has for the same package.
                "dependent_packages": 0,
                "dependent_repos": 0,
                "install_command": f"conda install -c conda-forge {data['name']}",
                "registry_url": data.get("html_url"),
                "downloads": data.get("ndownloads") or 0,
                "downloads_period": "total",
            }
        return None

    @staticmethod
    def _points_at(link: str, target: str) -> bool:
        """Whether a URL is this GitHub repository (scheme, www, .git and a
        trailing slash or subpath ignored)."""
        link = link.lower().split("://", 1)[-1].removeprefix("www.")
        link = link.removesuffix("/").removesuffix(".git")
        return link == target or link.startswith(target + "/")

    @staticmethod
    def _normalize(p: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "ecosystem": p.get("ecosystem"),
            "name": p.get("name"),
            "dependent_packages": p.get("dependent_packages_count") or 0,
            "dependent_repos": p.get("dependent_repos_count") or 0,
            "install_command": p.get("install_command"),
            "registry_url": p.get("registry_url"),
            "downloads": p.get("downloads") or 0,
            "downloads_period": p.get("downloads_period"),
        }

    @staticmethod
    def _drop_spurious_go_entries(
        registries: List[Dict[str, Any]], primary_language: Optional[str]
    ) -> List[Dict[str, Any]]:
        """Drop a "go" registry entry that carries zero dependents for a
        repo whose own primary language isn't Go.

        Go's decentralized module system lets any public repository path be
        `go get`-ed without the project ever intending to publish a Go
        module, so ecosyste.ms can index a non-Go repo under "go" with no
        real dependents. Left alone for a repo whose primary language
        actually is Go (a real, young package can legitimately have zero
        dependents yet), and left alone whenever the primary language isn't
        known at all -- absence of information isn't license to discard
        real data.
        """
        if not primary_language or primary_language.lower() == "go":
            return registries
        return [
            r for r in registries
            if not (r["ecosystem"] == "go" and r["dependent_packages"] == 0 and r["dependent_repos"] == 0)
        ]

    @staticmethod
    def _merge(packages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Collapse duplicate entries for the same package.

        The same package is often indexed by two registries in one ecosystem
        (conda-forge and anaconda.org both carry `hdf5`), which would otherwise
        double-count its dependents. Keep the highest count seen for each.
        """
        merged: Dict[tuple, Dict[str, Any]] = {}
        for p in packages:
            key = (p["ecosystem"], p["name"])
            if key not in merged:
                merged[key] = dict(p)
                continue
            existing = merged[key]
            existing["dependent_packages"] = max(
                existing["dependent_packages"], p["dependent_packages"]
            )
            existing["dependent_repos"] = max(
                existing["dependent_repos"], p["dependent_repos"]
            )
            existing["install_command"] = existing["install_command"] or p["install_command"]
            existing["registry_url"] = existing["registry_url"] or p["registry_url"]
            existing["downloads"] = max(existing.get("downloads", 0), p.get("downloads", 0))
            existing["downloads_period"] = existing.get("downloads_period") or p.get("downloads_period")
        return sorted(
            merged.values(), key=lambda r: (-r["dependent_packages"], r["ecosystem"])
        )

    # ---------------------------------------------------------------- scoring

    @staticmethod
    def _registry_evidence(registries: List[Dict[str, Any]], lookups: Dict) -> Dict[str, Any]:
        """Each package found, with its registry page, and for a registry
        with none, the names tried there. A lookup that failed, or that
        didn't run, claims nothing."""
        found = [{"path": f"{r['name']} ({r['ecosystem']})", "url": r.get("registry_url")}
                 for r in registries]
        missing, searched = [], []
        ecosystems = {r["ecosystem"] for r in registries}

        by_repo = lookups.get("repository", {})
        if by_repo.get("url") and not by_repo.get("failed"):
            searched.append({"text": "Package registries searched (via ecosyste.ms) for packages "
                                     "whose repository URL is",
                             "paths": [{"path": by_repo["url"]}]})
        spack = lookups.get("spack", {})
        if "spack" not in ecosystems:
            if spack.get("matched_by_spack"):
                # The orchestrator ran Spack and it has no recipe for this repository.
                searched.append({"text": "Spack's recipes (builtin, and any spack_repo in the catalog) checked "
                                         "for one whose homepage, url or git is, or that is named after",
                                 "paths": [{"path": spack.get("url") or "this repository"}]})
            elif spack.get("names") and not spack.get("failed"):
                places = list(spack["names"])
                if spack.get("by_download_url"):
                    places.append("any recipe whose homepage or download URL is this repository")
                searched.append({"text": "Spack recipes looked for by name",
                                 "paths": [{"path": p} for p in places]})
        conda = lookups.get("conda", {})
        if "conda" not in ecosystems and conda.get("names") and not conda.get("failed"):
            # A same-named package that doesn't point back here is the one
            # case a project can fix itself, by linking its feedstock.
            for pkg in conda.get("unlinked", []):
                missing.append({"text": f"a link to this repository from conda-forge's {pkg['name']} "
                                        "(its dev_url, home and source_git_url point elsewhere, "
                                        "so it isn't credited)"})
            searched.append({"text": "conda-forge packages looked for by name",
                             "paths": [{"path": n} for n in conda["names"]]})
        return {"found": found, "missing": missing, "searched": searched}

    @staticmethod
    def _reverse_dependency_evidence(registries: List[Dict[str, Any]]) -> Dict[str, Any]:
        """The package behind each of 4.1.1's two figures: they are the
        largest counts across packages, so can come from different ones."""
        found = []
        for key, what in [("dependent_packages", "dependent packages"),
                          ("dependent_repos", "dependent repositories")]:
            top = max(registries, key=lambda r: r[key], default=None)
            if top and top[key]:
                found.append({"text": f"{top[key]:,} {what}",
                              "path": f"{top['name']} ({top['ecosystem']})",
                              "url": top.get("registry_url")})
        return {"found": found}

    @classmethod
    def _install_evidence(cls, registries: List[Dict[str, Any]], lookups: Dict) -> Dict[str, Any]:
        """Each package a user can install, with the command; with none,
        the packages found that give no install path and where the
        registries were searched."""
        found = [{"path": f"{r['name']} ({r['ecosystem']})", "url": r.get("registry_url"),
                  "quote": r["install_command"]}
                 for r in registries if r.get("install_command")]
        if found:
            return {"found": found}
        registry = cls._registry_evidence(registries, lookups)
        missing = [{"text": f"an install command for {r['name']} ({r['ecosystem']})"}
                   for r in registries]
        return {"missing": missing + registry["missing"], "searched": registry["searched"]}

    @staticmethod
    def _network_evidence(
        registries: List[Dict[str, Any]], source: Optional[Dict[str, Any]], lookups: Dict,
    ) -> Dict[str, Any]:
        """Which package each dependent count came from, and the
        dependency-audit graph used or where it was looked for."""
        found = [
            {"text": f"{r['name']} ({r['ecosystem']}): {r['dependent_packages']:,} dependent "
                     f"packages, {r['dependent_repos']:,} dependent repositories"}
            for r in registries if r["dependent_packages"] or r["dependent_repos"]
        ]
        missing, searched = [], []
        audit = lookups.get("audit", {})
        graph = audit.get("path") or "dependency-audit graph"
        if source:
            found.append({"text": f"{source.get('count', 0):,} source-level dependents",
                          "path": graph, "url": source.get("url")})
        elif audit.get("outcome") == "absent":
            missing.append({"text": "a dependency-audit graph for this repository"})
            searched.append({"text": "Dependency-audit graph looked for at",
                             "paths": [{"path": graph}]})
        elif audit.get("outcome") not in (None, "failed", "used"):
            missing.append({"text": f"a usable dependency-audit graph: {audit['outcome']}"})
            searched.append({"text": "Dependency-audit graph read", "paths": [{"path": graph}]})
        return {"found": found, "missing": missing, "searched": searched}

    def _calculate_score(
        self, registries: List[Dict[str, Any]], source: Optional[Dict[str, Any]] = None,
        lookups: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        lookups = lookups or {}
        ecosystems = sorted({r["ecosystem"] for r in registries})
        max_packages = max((r["dependent_packages"] for r in registries), default=0)
        # Registry and source-level dependents are found different ways and
        # can overlap, so the larger count stands rather than their sum.
        source_repos = (source or {}).get("count", 0)
        max_repos = max(max((r["dependent_repos"] for r in registries), default=0), source_repos)

        sub: Dict[str, Dict[str, Any]] = {}

        sub["advanced_dependency_analysis"] = {
            "label": "Advanced Dependency Analysis",
            "value": f"{len(ecosystems)} ecosystem{'s' if len(ecosystems) != 1 else ''}: "
                     + ", ".join(ecosystems)
                     if ecosystems else "Not packaged in any indexed ecosystem",
            "passing": len(ecosystems) >= get_threshold("ecosystem/collaboration", "Advanced Dependency Analysis"),
            "evidence": self._registry_evidence(registries, lookups),
        }

        reach = (
            max_packages >= get_threshold("ecosystem/collaboration", "Collaboration Network Analysis", "min_dependent_packages")
            or max_repos >= get_threshold("ecosystem/collaboration", "Collaboration Network Analysis", "min_dependent_repos")
        )
        sub["collaboration_network"] = {
            "label": "Collaboration Network Analysis",
            "value": f"{max_packages:,} dependent packages, {max_repos:,} dependent repositories"
                     if registries or source_repos else "No downstream dependents found",
            "detail": (f"{source_repos:,} source-level dependents found by dependency audit"
                       + ("" if source.get("complete") else " (partial search)"))
                      if source_repos else None,
            "passing": reach,
            "evidence": self._network_evidence(registries, source, lookups),
        }
        # Registries can't see source-level use, so without a dependency-audit
        # graph a registry-only shortfall isn't a confirmed lack of dependents.
        # A registry count that already clears the bar still stands.
        if source is None and not reach:
            sub["collaboration_network"].update({
                "value": (sub["collaboration_network"]["value"]
                          + " in registries; source-level dependents not audited"),
                "not_collected": True, "unmeasured": True,
            })

        for key, label in [
            ("cross_project_reference", "Cross-project Reference Detection"),
            ("interoperability", "Interoperability Assessment"),
            ("standards_compliance", "Standards Compliance Tracking"),
        ]:
            sub[key] = {"label": label, "value": None, "passing": False, "not_collected": True}

        # Consumed by CASS 4.3.4, which rests on the same registry data:
        # how many ways a user can install the software without building it.
        # Count distinct ecosystems, not packages: conda carrying both `hdf5`
        # and `hdf5-static` is one package manager a user can install from,
        # not two.
        installable = sorted({
            r["ecosystem"] for r in registries if r.get("install_command")
        })
        sub["installation_success"] = {
            "label": "Installation Success Tracking",
            "value": f"Installable from {len(installable)} package manager"
                     f"{'s' if len(installable) != 1 else ''}: " + ", ".join(installable)
                     if installable else "No package-manager install path found",
            "passing": bool(installable),
            # The row renders in 4.3.4 (Quality) though this collector is
            # Ecosystem's, so the report matches it by section, not dimension.
            "section": "quality/usability",
            "evidence": self._install_evidence(registries, lookups),
        }

        collaboration_keys = [
            "advanced_dependency_analysis", "cross_project_reference",
            "interoperability", "collaboration_network", "standards_compliance",
        ]
        # Only measured rows count: the three uncollected stubs (and an
        # unmeasured network row) used to be scored as failures, pulling
        # every package's Ecosystem score down.
        scorable = [k for k in collaboration_keys if not sub[k].get("not_collected")]
        score = sum(1 for k in scorable if sub[k].get("passing"))
        return {
            "score": score,
            "max_score": len(scorable),
            "percentage": round(score / len(scorable) * 100, 2) if scorable else None,
            "sub_scores": sub,
        }

    async def _source_dependents(
        self, client: httpx.AsyncClient, owner: str, repo: str, trace: Optional[Dict] = None,
    ) -> Optional[Dict[str, Any]]:
        """Direct source-level dependents from the project's dependency-audit
        graph, or None when there's no graph, it's for a different repository,
        or it predates confidence scoring (unscored matches include obvious
        false positives for generic names, so they aren't used).

        Counts DEPENDS_ON edges into the project rated high or medium
        confidence, from repositories outside its own organization; vendored
        copies and mirrors are excluded.

        `trace`, if given, records the graph's path and, when None is
        returned, why: "failed", "absent", or the reason it wasn't usable.
        """
        trace = {} if trace is None else trace
        trace["path"] = _DEPENDENCY_AUDIT_PATH.format(name=repo.lower())
        graph = await self._get_json(client, _DEPENDENCY_AUDIT.format(name=repo.lower()))
        if not isinstance(graph, dict):
            trace["outcome"] = "failed" if graph is _FETCH_FAILED else "absent"
            return None
        root = f"{owner}/{repo}".lower()
        if str((graph.get("meta") or {}).get("root", "")).lower() != root:
            trace["outcome"] = "the graph published under this name is for another repository"
            return None
        edges = [e for e in graph.get("edges", []) if str(e.get("target", "")).lower() == root]
        if not edges:
            trace["outcome"] = "the graph records no dependents of this repository"
            return None
        if not any("confidence" in e and "relationship" in e for e in edges):
            trace["outcome"] = "the graph predates confidence scoring"
            return None
        trace["outcome"] = "used"
        dependents = {
            e["source"].lower() for e in edges
            if e.get("relationship") == "DEPENDS_ON" and e.get("confidence") in _AUDIT_CONFIDENCE
            and not e["source"].lower().startswith(f"{owner.lower()}/")
        }
        complete = bool(((graph.get("meta") or {}).get("completeness") or {}).get("complete"))
        return {"count": len(dependents), "complete": complete,
                "url": _DEPENDENCY_AUDIT.format(name=repo.lower())}

    @staticmethod
    def _downloads_summary(registries: List[Dict[str, Any]]) -> Dict[str, Any]:
        by_registry = [
            {
                "ecosystem": r["ecosystem"],
                "name": r["name"],
                "downloads": r.get("downloads", 0),
                "period": r.get("downloads_period"),
            }
            for r in registries if r.get("downloads")
        ]
        return {
            "total": sum(r["downloads"] for r in by_registry),
            "by_registry": by_registry,
        }

    def _empty_result(self, repo_name: str) -> Dict[str, Any]:
        return {
            "package_name": repo_name,
            "repository": "unknown",
            "timestamp": self.forge.get_timestamp(),
            "registries": [],
            "ecosystems": [],
            "overall_score": self._calculate_score([]),
            "package_downloads": {"total": 0, "by_registry": []},
        }
