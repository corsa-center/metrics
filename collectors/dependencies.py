"""
Dependency collectors: what a package depends on, and what depends on it.

These sit outside the three dimensions. Their results are metrics.json's
`dependencies` and `spack` keys, which tools/dashboard_data.py turns into
the dashboard's dependency and Spack pages; the Spack match is also handed
to the dimension collectors (with_spack_match).

SpackDependencyCollector runs once per batch; DependencyCollector runs per
package and adds that batch's Spack entry to the forge's dependency graph.
"""

import asyncio
import json
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from forge.base import COLLECTION_GAP, RetryingTransport
from forge.interface import Forge

logger = logging.getLogger(__name__)

_SPACK_SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "spack_dependencies.py"


class SpackDependencyCollector:
    """Spack's dependency data for a batch of packages (see collect)."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config

    async def collect(
        self, packages: List[Dict], spack_repos: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Optional[Dict]]]:
        """Spack's dependency data for the packages, keyed by repo_url:
        {name, dependencies, dependents} for the Spack package built from
        that repository (see tools/spack_dependencies.py), or None when
        Spack has none. None overall when Spack isn't on PATH or the
        analysis failed -- a gap, so the metrics files leave the `spack` key
        out.

        One `spack python` run covers the whole batch, since finding a
        package's dependents means loading every package Spack has. The
        package repositories in `spack_repos` are searched after Spack's
        own, for every package: a recipe there can depend on any package
        (SCOREC's pcms on hdf5 and adios2), so it is one of their dependents
        too. A package's `spack_package`, when set, names its recipe.
        """
        spack = shutil.which("spack")
        if spack is None:
            logger.info("spack is not on PATH; skipping Spack dependency data")
            return None
        repo_urls = sorted({p["repo_url"] for p in packages if p.get("repo_url")})
        if not repo_urls:
            return {}
        chosen = self._spack_packages(packages)
        spack_config = self.config.get("spack") or {}
        timeout = float(spack_config.get("timeout", 1800))
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "spack.json"
            # Without a repository, its packages would be recorded as not in
            # Spack; better a gap, so the last good day's data is kept.
            extra = await self._clone_repos(spack_repos or [], Path(tmp), 300)
            if extra is None:
                return None
            try:
                proc = await asyncio.create_subprocess_exec(
                    spack, "python", str(_SPACK_SCRIPT), "--output", str(output),
                    *(arg for path in extra for arg in ("--repo", path)),
                    *(arg for url, name in sorted(chosen.items()) for arg in ("--package", f"{name}={url}")),
                    *repo_urls,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
                )
                try:
                    _, stderr = await asyncio.wait_for(proc.communicate(), timeout)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                    logger.warning(f"Spack dependency analysis timed out after {timeout:.0f}s")
                    return None
                if proc.returncode != 0:
                    logger.warning(
                        f"Spack dependency analysis failed (exit {proc.returncode}): "
                        f"{stderr.decode(errors='replace')[-2000:]}"
                    )
                    return None
                result = json.loads(output.read_text())
                self._log_matches(result, chosen)
                return result
            except (OSError, ValueError) as e:
                logger.warning(f"Spack dependency analysis failed: {e!r}")
                return None

    @staticmethod
    async def _clone_repos(urls: List[str], into: Path, timeout: float) -> Optional[List[str]]:
        """Shallow clones of the package repositories at `urls`, as the
        directories holding their repo.yaml, or None if any clone failed."""
        paths = []
        for i, url in enumerate(urls):
            dest = into / f"repo{i}"
            try:
                proc = await asyncio.create_subprocess_exec(
                    "git", "clone", "--quiet", "--depth", "1", url, str(dest),
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout)
            except (OSError, asyncio.TimeoutError) as e:
                logger.warning(f"Cloning Spack repository {url} failed: {e!r}")
                return None
            if proc.returncode != 0:
                logger.warning(f"Cloning Spack repository {url} failed: {stderr.decode(errors='replace')[-500:]}")
                return None
            # repo.yaml sits at the root, or under spack_repo/<namespace>/.
            found = sorted(dest.glob("repo.yaml")) or sorted(dest.glob("spack_repo/*/repo.yaml"))
            if not found:
                logger.warning(f"Spack repository {url} has no repo.yaml")
                return None
            paths += [str(f.parent) for f in found]
        return paths

    @staticmethod
    def spack_repos(catalog_packages) -> List[str]:
        """The package repositories catalog entries name in `spack_repo`
        (a git URL, or a list of them) for recipes Spack's builtin
        repository lacks."""
        urls = set()
        for package in catalog_packages:
            value = package.get("spack_repo")
            for url in [value] if isinstance(value, str) else value if isinstance(value, list) else []:
                if isinstance(url, str) and url.strip():
                    urls.add(url.strip())
        return sorted(urls)

    @staticmethod
    def _spack_packages(packages: List[Dict]) -> Dict[str, str]:
        """{repo_url: name} for the catalog entries that name their Spack
        package outright in `spack_package` -- a monorepo whose recipe to
        show isn't the one the matching picks (darshan-hpc/darshan builds
        darshan-util and darshan-runtime)."""
        chosen = {}
        for package in packages:
            name = package.get("spack_package")
            if package.get("repo_url") and isinstance(name, str) and name.strip():
                chosen[package["repo_url"]] = name.strip()
        return chosen

    @staticmethod
    def _log_matches(result: Dict[str, Optional[Dict]], chosen: Dict[str, str]) -> None:
        """Which repositories got a Spack package, and which didn't: one of
        the latter that does have a recipe outside Spack's builtin
        repository needs `spack_repo` in its catalog entry. A `spack_package`
        Spack has no recipe for is matched as if it weren't set, and warned
        about."""
        def short(url):
            return urlparse(url).path.strip("/").removesuffix(".git") or url
        found = [f"{short(u)} -> {e['name']}" for u, e in sorted(result.items()) if e]
        missing = [short(u) for u, e in sorted(result.items()) if not e]
        logger.info(f"Spack packages found for {len(found)} of {len(result)} repositories: {', '.join(found)}")
        if missing:
            logger.info(f"Not in Spack: {', '.join(missing)}")
        for url, name in sorted(chosen.items()):
            entry = result.get(url)
            if url in result and (entry or {}).get("name") != name:
                logger.warning(f"{short(url)}: spack_package {name!r} is not a Spack package; "
                               f"matched {entry['name'] if entry else 'none'} instead")


def with_spack_match(package: Dict, spack_data: Optional[Dict[str, Optional[Dict]]]) -> Dict:
    """The package as the dimension collectors see it, with its Spack match
    from `spack_data` (SpackDependencyCollector.collect's result for this
    batch) added as `spack_match`.

    Three cases, which the collectors tell apart:
      - no `spack_match` key: Spack wasn't analyzed (a run without
        Spack), so collectors fall back to looking the package up by name;
      - `spack_match` is None: Spack has no recipe for this repository;
      - `spack_match` is {"name", "dependent_packages"}: the recipe
        Spack matched, and how many Spack packages depend on it.

    Returns a copy, because the package itself is written out as
    metrics.json's `metadata` and this shouldn't appear there.
    """
    repo_url = package.get("repo_url")
    if spack_data is None or repo_url not in spack_data:
        return package

    entry = spack_data[repo_url]
    if entry:
        dependents = entry.get("dependents") or {}
        spack_match = {"name": entry["name"], "dependent_packages": len(dependents)}
    else:
        spack_match = None

    package_for_collectors = dict(package)
    package_for_collectors["spack_match"] = spack_match
    return package_for_collectors


class DependencyCollector:
    """A package's forge dependency graph and its Spack entry."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config

    def forge_for(self, package: Dict) -> Optional[Forge]:
        """The Forge for a package, chosen by its repo_type (see Forge.for_repo)."""
        return Forge.for_repo(
            package.get("repo_type"),
            package.get("repo_url", ""),
            self.config.get("api_credentials", {}),
        )

    async def collect(self, package: Dict, spack_data: Optional[Dict[str, Optional[Dict]]]) -> Dict:
        """The package's `dependencies` (the forge's dependency graph) and
        `spack` (its Spack dependencies and dependents, from `spack_data`,
        SpackDependencyCollector.collect's result for this batch). A key is
        left out when its lookup was a gap, so tools/dashboard_data.py can
        keep an earlier day's entry instead of publishing a blank one.
        """
        info: Dict = {}
        if spack_data is not None and package.get("repo_url") in spack_data:
            info["spack"] = spack_data[package["repo_url"]]

        forge = self.forge_for(package)
        ref = forge.extract_ref(package["repo_url"]) if forge else None
        if ref is None:
            return info
        try:
            async with httpx.AsyncClient(timeout=60.0, transport=RetryingTransport()) as client:
                dependencies = await forge.dependency_graph(client, ref)
        except Exception as e:
            logger.warning(f"dependencies failed for {package['repo_url']}: {e!r}")
        else:
            if dependencies is not COLLECTION_GAP:
                info["dependencies"] = dependencies
        return info
