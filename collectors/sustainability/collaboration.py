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

The collector also produces the Installation Success Tracking figure that CASS
section 4.3.4 needs, since it rests on the same registry data.

Not collected: Cross-project Reference Detection (the report specifies AI
analysis of issues and PRs), Interoperability Assessment and Standards
Compliance Tracking (both need domain-specific standards knowledge).
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx

from collectors.sustainability.base import GitHubCollectorBase

logger = logging.getLogger(__name__)

_PACKAGES_API = "https://packages.ecosyste.ms/api/v1"

# Being packaged for more than one ecosystem is what "multi-platform ecosystem
# mapping" is asking about.
_MIN_ECOSYSTEMS = 2

# Downstream reach. Either signal alone is enough: a library can be depended on
# by many packages (HDF5: 176 conda packages) or by many repositories
# (zfp: 111), and both are real evidence of ecosystem integration.
_MIN_DEPENDENT_PACKAGES = 10
_MIN_DEPENDENT_REPOS = 50

# ecosyste.ms publishes a low per-second rate limit; one retry with a pause
# covers the throttling seen when several lookups run back to back.
_RATE_LIMIT_PAUSE_SECONDS = 2


class CollaborationCollector(GitHubCollectorBase):
    """Collects ecosystem integration metrics (Section 4.2.7)."""

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        repo_name = package.get("name", "Unknown")
        repo_url = package.get("repo_url", "")
        owner_repo = self._extract_owner_repo(repo_url)
        if not owner_repo:
            logger.error(f"Could not extract owner/repo from {repo_url}")
            return self._empty_result(repo_name)

        owner, repo = owner_repo
        logger.info(f"Collecting collaboration metrics for {repo_name}")

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            by_repo, spack = await asyncio.gather(
                self._lookup_by_repository(client, owner, repo),
                self._lookup_spack(client, repo),
                return_exceptions=True,
            )

        if isinstance(by_repo, Exception):
            logger.warning(f"ecosyste.ms repository lookup failed: {by_repo}")
            by_repo = []
        if isinstance(spack, Exception):
            logger.warning(f"Spack lookup failed: {spack}")
            spack = None

        registries = self._merge(by_repo + ([spack] if spack else []))
        return {
            "package_name": repo_name,
            "repository": f"{owner}/{repo}",
            "timestamp": self._get_timestamp(),
            "registries": registries,
            "ecosystems": sorted({r["ecosystem"] for r in registries}),
            "overall_score": self._calculate_score(registries),
        }

    # ------------------------------------------------------------------ fetch

    async def _get_json(self, client: httpx.AsyncClient, url: str) -> Optional[Any]:
        """GET a JSON document, retrying once if the API throttles."""
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
                return None
            except Exception as e:
                if attempt == 0:
                    logger.debug(f"Error fetching {url}: {e}, retrying…")
                    await asyncio.sleep(1)
                    continue
                return None
        return None

    async def _lookup_by_repository(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> List[Dict[str, Any]]:
        """Every package ecosyste.ms links back to this repository."""
        target = quote(f"https://github.com/{owner}/{repo}", safe="")
        data = await self._get_json(
            client, f"{_PACKAGES_API}/packages/lookup?repository_url={target}"
        )
        if not isinstance(data, list):
            return []
        return [self._normalize(p) for p in data if p.get("ecosystem") and p.get("name")]

    async def _lookup_spack(
        self, client: httpx.AsyncClient, repo: str
    ) -> Optional[Dict[str, Any]]:
        """Spack recipe for this project, looked up by name.

        Tried lower-cased as well, since Spack package names are lower-case by
        convention while repository names often are not (ADIOS2 -> adios2).
        """
        for name in dict.fromkeys([repo, repo.lower()]):
            data = await self._get_json(
                client, f"{_PACKAGES_API}/registries/spack.io/packages/{quote(name)}"
            )
            if isinstance(data, dict) and data.get("name"):
                return self._normalize(data)
        return None

    @staticmethod
    def _normalize(p: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "ecosystem": p.get("ecosystem"),
            "name": p.get("name"),
            "dependent_packages": p.get("dependent_packages_count") or 0,
            "dependent_repos": p.get("dependent_repos_count") or 0,
            "install_command": p.get("install_command"),
            "registry_url": p.get("registry_url"),
        }

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
        return sorted(
            merged.values(), key=lambda r: (-r["dependent_packages"], r["ecosystem"])
        )

    # ---------------------------------------------------------------- scoring

    def _calculate_score(self, registries: List[Dict[str, Any]]) -> Dict[str, Any]:
        ecosystems = sorted({r["ecosystem"] for r in registries})
        max_packages = max((r["dependent_packages"] for r in registries), default=0)
        max_repos = max((r["dependent_repos"] for r in registries), default=0)

        sub: Dict[str, Dict[str, Any]] = {}

        sub["advanced_dependency_analysis"] = {
            "label": "Advanced Dependency Analysis",
            "value": f"{len(ecosystems)} ecosystem{'s' if len(ecosystems) != 1 else ''}: "
                     + ", ".join(ecosystems)
                     if ecosystems else "Not packaged in any indexed ecosystem",
            "passing": len(ecosystems) >= _MIN_ECOSYSTEMS,
        }

        reach = (
            max_packages >= _MIN_DEPENDENT_PACKAGES or max_repos >= _MIN_DEPENDENT_REPOS
        )
        sub["collaboration_network"] = {
            "label": "Collaboration Network Analysis",
            "value": f"{max_packages:,} dependent packages, {max_repos:,} dependent repositories"
                     if registries else "No downstream dependents found",
            "passing": reach,
        }

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
        }

        collaboration_keys = [
            "advanced_dependency_analysis", "cross_project_reference",
            "interoperability", "collaboration_network", "standards_compliance",
        ]
        score = sum(1 for k in collaboration_keys if sub[k].get("passing"))
        return {
            "score": score,
            "max_score": len(collaboration_keys),
            "percentage": round(score / len(collaboration_keys) * 100, 2),
            "sub_scores": sub,
        }

    def _empty_result(self, repo_name: str) -> Dict[str, Any]:
        return {
            "package_name": repo_name,
            "repository": "unknown",
            "timestamp": self._get_timestamp(),
            "registries": [],
            "ecosystems": [],
            "overall_score": self._calculate_score([]),
        }
