#!/usr/bin/env python3
"""
Orchestrator: Coordinates metrics collection and dashboard integration

Collects metrics across the three CASS dimensions defined in the
CASS Sustainability Metrics Report v3:
  - Impact (4.1): Citation, adoption, field research impact
  - Ecosystem (4.2): Governance, licensing, maintenance, engagement, etc.
  - Quality (4.3): Reliability, dev practices, reproducibility, usability, etc.

Usage:
    python orchestrator.py --config config.yaml [--dry-run] [--software HDF5]
"""

import argparse
import asyncio
import json
import logging
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx
import re
import yaml

from catalog.catalog import Catalog
from collectors.common import configure_threshold_overrides
from collectors.ecosystem.dimension import EcosystemDimensionCollector
from collectors.impact.dimension import ImpactDimensionCollector
from collectors.quality.dimension import QualityDimensionCollector
from formatters.dashboard import DashboardFormatter
from forge.base import (
    COLLECTION_GAP,
    DEFINITE_METADATA_KEYS,
    NULLABLE_METADATA_KEYS,
    RetryingTransport,
)
from forge.interface import Forge
from package_report import render_package_report

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class MetricsOrchestrator:
    """Orchestrates metrics collection and dashboard integration"""

    def __init__(self, config_path: str):
        """Initialize orchestrator with configuration

        Args:
            config_path: Path to configuration file
        """
        self.config = self._load_config(config_path)
        self._configure_logging()
        # Overrides for config/thresholds.yaml's defaults (see that file).
        # Installed before any collector's collect() can run, so every
        # get_threshold() call during this process sees the final value.
        # An override referencing a threshold that isn't declared in
        # thresholds.yaml raises here, at startup, instead of being
        # silently ignored.
        configure_threshold_overrides(self.config.get("thresholds"))
        self.catalog = Catalog(self.config.get("catalog_url", ""), self.config.get("api_credentials", {}))
        # One collector per dimension (collectors/<dimension>/dimension.py),
        # each reading its own on/off and per-sub-collector toggles.
        self.dimensions = {
            "impact": ImpactDimensionCollector(self.config),
            "ecosystem": EcosystemDimensionCollector(self.config),
            "quality": QualityDimensionCollector(self.config),
        }
        # Turns the dimensions' raw results into the dashboard's sections
        # (formatters/); another Formatter can present them differently.
        self.formatter = DashboardFormatter()
        # Catalog repo_url -> collect_spack_dependencies() entry, or None
        # until a batch has been analyzed (or when Spack isn't available).
        self.spack_data: Optional[Dict[str, Optional[Dict]]] = None

    def _configure_logging(self) -> None:
        """Wire up config/orchestrator.yaml's `logging:` block.

        It was declared (level/file/console) but never actually read --
        the module-level logging.basicConfig() above runs at import time,
        before any config exists, hardcoded to stdout only. So
        `file: "orchestrator.log"` has never actually been written, and
        answering "why did this metric come back empty for this package"
        has meant reproducing the collector by hand locally, which is how
        every "why" in the 2026-09-16 incident actually got diagnosed.
        Adds a FileHandler to the *root* logger (not just this module's),
        since every collector's `logging.getLogger(__name__)` propagates up
        to it by default -- this one change makes every existing
        logger.warning/logger.error call across every collector durable.
        """
        log_config = self.config.get("logging", {})
        level = getattr(logging, str(log_config.get("level", "INFO")).upper(), logging.INFO)
        root = logging.getLogger()
        root.setLevel(level)

        log_file = log_config.get("file")
        if log_file and not any(
            isinstance(h, logging.FileHandler) and h.baseFilename == os.path.abspath(log_file)
            for h in root.handlers
        ):
            handler = logging.FileHandler(log_file)
            handler.setFormatter(
                logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
            )
            root.addHandler(handler)

    def _load_config(self, config_path: str) -> Dict:
        """Load configuration from YAML file, resolving ${ENV_VAR} references"""
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
        return self._resolve_env_vars(config)

    def _resolve_env_vars(self, obj):
        """Recursively resolve ${ENV_VAR} references in config values"""
        if isinstance(obj, str):
            return re.sub(
                r"\$\{(\w+)\}",
                lambda m: os.environ.get(m.group(1), ""),
                obj,
            )
        if isinstance(obj, dict):
            return {k: self._resolve_env_vars(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._resolve_env_vars(v) for v in obj]
        return obj


    async def prepare_software_list(
        self,
        filter_software: Optional[str] = None,
        group: Optional[int] = None,
        group_count: Optional[int] = None,
        max_packages: Optional[int] = None,
        name: Optional[str] = None,
        repo: Optional[str] = None,
        repo_type: Optional[str] = None,
        processed_packages: Optional[Dict] = None,
    ) -> Tuple[Dict, List[str]]:
        """Prepare list of software packages to process. There are a number of ways
        that this list can be generated.

        1. Using the group and group_count options to process packages in a round-robin
           fashion. This can be used to limit processing to avoid overloading APIs or
           timeouts (e.g. by a GitHub action). The current group will need to be saved
           externally somewhere. The filter_software and max_packages options can be
           used to restrict packages further.

        2. The processed_packages option uses the summary report to record which packages have
           been processed so that these are not processed again. This is usually combined
           with the max_packages to limit how many packages are processed in a batch. The
           orchestrator can then be repeatedly called until all packages have been processed.
           It's not expected that the filter_software option would be used in this case.

        3. For a single package using the repo, repo-type, and name options. The filter_software
           option doesn't make sense in this case.

        Args:
            filter_software: Optional name filter for specific software
            group: Which slice to keep, 0-indexed (requires group_count)
            group_count: Split the catalog into this many round-robin
                slices, keeping only every Nth package starting at `group`.
                Exists because a full-portfolio run's GitHub API request
                volume -- already close to the hourly quota before any
                retries -- doesn't fit in one run once throttled requests
                get retried instead of silently dropped. See
                config/orchestrator.yaml rate_limiting comments. Ignored
                when filter_software is set, since targeting one package
                should never depend on which group it happens to fall in.
            max_packages: Stop after this many packages; 0 processes none.
            name: Catalog name for the package, required with repo since it
                keys the metrics output.
            repo: URL of a single repo to scan instead of the catalog
            repo_type: Type of repo to scan, must be provided if repo is specified
            processed_packages: Names of packages that have already been processed
                to use to filter which packages to process.

        Returns:
            Software catalog and names of the packages to process
        """
        if repo:
            if not name:
                raise ValueError("name is required when repo is given")
            if not repo_type:
                raise ValueError("repo type is required when repo is given")
            catalog = self.catalog.to_catalog(name, repo, repo_type)
        else:
            catalog = await self.catalog.load()

        software_list = []
        for name, package in catalog.items():
            if not package.get("repo_url", 0):
                logger.warning(f"Skipping {name} because no repository url found")
                continue
            # Apply filter if specified
            if filter_software and filter_software.lower() not in name.lower():
                continue
            # Skip package already processed
            if processed_packages and name in processed_packages:
                continue
            software_list.append(name)

        if group_count and group_count > 1 and not filter_software:
            # Sort first so the slice is stable regardless of any incidental
            # reordering of the catalog JSON between runs.
            software_list.sort()
            software_list = software_list[group::group_count]
            logger.info(f"Group {group}/{group_count}: {len(software_list)} packages")

        # `is not None` so --max-packages 0 means none, not "no limit".
        if max_packages is not None and max_packages >= 0:
            software_list = software_list[:max_packages]

        logger.info(f"Prepared {len(software_list)} software packages for processing")
        return catalog, software_list


    def _forge_for_package(self, package: Dict) -> Optional[Forge]:
        """The Forge for a package, chosen by its repo_type (see Forge.for_repo)."""
        return Forge.for_repo(
            package.get("repo_type"),
            package.get("repo_url", ""),
            self.config.get("api_credentials", {}),
        )

    async def _get_repo_info(self, forge: Forge, repo_url: str) -> Dict:
        ref = forge.extract_ref(repo_url)
        if ref is None:
            return {}
        try:
            async with httpx.AsyncClient(timeout=30.0, transport=RetryingTransport()) as client:
                return await forge.repo_info(client, ref)
        except Exception as e:
            # repo_info's own helpers already absorb 404s, rate limits and
            # 5xx, so reaching here means something unexpected (a malformed
            # 200 body, for one). Report it as a gap rather than a confirmed
            # absence, but don't let it vanish -- a silent gap looks exactly
            # like a healthy repo with nothing to say.
            logger.warning(f"repo_info failed for {repo_url}: {e!r}")
            return COLLECTION_GAP

    async def _update_repo_info(self, package: Dict) -> Tuple[bool, str]:
        """Copy repository metadata onto the package, and say whether to collect.

        Doubles as the catalog-identity preflight (METRIC_BLIND_SPOTS.md
        F13): one lookup answers both "does this repository exist?" and
        "what does it say about itself?".

        Args:
            package: package to update, in place

        Returns:
            (collect, reason). collect is False only when we know collection
            is pointless -- an unsupported platform, or a repository the
            forge confirms isn't there. A gap (rate limit, network trouble)
            returns True: we don't know, so the collectors run and report
            their own "not collected" rather than scoring a false zero.
            reason explains a False, and is "" otherwise.
        """
        forge = self._forge_for_package(package)
        if forge is None:
            # Not on a supported platform. This will leave all sub-metrics unset ("not
            # yet collected" downstream) rather than let each collector
            # silently 404 against the wrong host and report a false
            # "not found"/"failing" result.
            return False, (f"repo_type {package.get('repo_type')!r} at {package['repo_url']} "
                           f"is not a supported platform")

        repo_info = await self._get_repo_info(forge, package["repo_url"])
        if repo_info:
            # Only when the lookup actually returned data -- on a gap the
            # defaults would overwrite curated catalog values, and write a
            # default_branch of "HEAD" for a repo we never reached.
            package.update(
                (k, repo_info[k]) for k in NULLABLE_METADATA_KEYS if repo_info.get(k)
            )
            package.update(
                (k, repo_info[k])
                for k in DEFINITE_METADATA_KEYS
                if repo_info.get(k) is not None
            )
            package.update(await self._get_repo_activity(forge, package))

        if repo_info is not COLLECTION_GAP and not repo_info:
            return False, f"unable to access {package['repo_url']}"

        return True, ""

    async def _get_repo_activity(self, forge: Forge, package: Dict) -> Dict:
        """Pull request, issue and mentionable-user counts for the package's
        metadata, plus its primary language when repo_info had none
        (GitLab's project object doesn't carry one). A lookup that was a
        gap leaves its keys out, so tools/dashboard_data.py can fall back
        to an earlier day's values.
        """
        ref = forge.extract_ref(package["repo_url"])
        if ref is None:
            return {}
        activity: Dict = {}
        try:
            async with httpx.AsyncClient(timeout=60.0, transport=RetryingTransport()) as client:
                counts = await forge.activity_counts(client, ref)
                if counts:
                    activity.update(counts)
                if not package.get("language"):
                    languages = await forge.languages(client, ref)
                    if languages:
                        activity["language"] = max(languages, key=languages.get)
        except Exception as e:
            logger.warning(f"activity counts failed for {package['repo_url']}: {e!r}")
        return activity

    @staticmethod
    async def _clone_spack_repos(urls: List[str], into: Path, timeout: float) -> Optional[List[str]]:
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
    def _spack_repos(catalog_packages) -> List[str]:
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
    def _log_spack_matches(result: Dict[str, Optional[Dict]]) -> None:
        """Which repositories got a Spack package, and which didn't: one of
        the latter that does have a recipe outside Spack's builtin
        repository needs `spack_repo` in its catalog entry."""
        def short(url):
            return urlparse(url).path.strip("/").removesuffix(".git") or url
        found = [f"{short(u)} -> {e['name']}" for u, e in sorted(result.items()) if e]
        missing = [short(u) for u, e in sorted(result.items()) if not e]
        logger.info(f"Spack packages found for {len(found)} of {len(result)} repositories: {', '.join(found)}")
        if missing:
            logger.info(f"Not in Spack: {', '.join(missing)}")

    async def collect_spack_dependencies(
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
        too.
        """
        spack = shutil.which("spack")
        if spack is None:
            logger.info("spack is not on PATH; skipping Spack dependency data")
            return None
        repo_urls = sorted({p["repo_url"] for p in packages if p.get("repo_url")})
        if not repo_urls:
            return {}
        script = Path(__file__).resolve().parent / "tools" / "spack_dependencies.py"
        spack_config = self.config.get("spack") or {}
        timeout = float(spack_config.get("timeout", 1800))
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "spack.json"
            # Without a repository, its packages would be recorded as not in
            # Spack; better a gap, so the last good day's data is kept.
            extra = await self._clone_spack_repos(spack_repos or [], Path(tmp), 300)
            if extra is None:
                return None
            try:
                proc = await asyncio.create_subprocess_exec(
                    spack, "python", str(script), "--output", str(output),
                    *(arg for path in extra for arg in ("--repo", path)), *repo_urls,
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
                self._log_spack_matches(result)
                return result
            except (OSError, ValueError) as e:
                logger.warning(f"Spack dependency analysis failed: {e!r}")
                return None

    async def collect_dependency_info(self, package: Dict) -> Dict:
        """The package's `dependencies` (the forge's dependency graph) and
        `spack` (its Spack dependencies and dependents, from this batch's
        collect_spack_dependencies run). A key is left out when its lookup
        was a gap, so tools/dashboard_data.py can keep an earlier day's
        entry instead of publishing a blank one.
        """
        info: Dict = {}
        if self.spack_data is not None and package.get("repo_url") in self.spack_data:
            info["spack"] = self.spack_data[package["repo_url"]]

        forge = self._forge_for_package(package)
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

    async def collect_all_metrics(self, package: Dict) -> Dict:
        """Collect all metrics for a package across the 3 dimensions.

        Args:
            package: Package metadata

        Returns:
            Complete metrics dictionary
        """
        logger.info(
            f"Starting metrics collection for {package['name']} ({package['repo_url']})"
        )

        # Each dimension's default, kept when collection is skipped or the
        # dimension raises.
        results = {name: dim.empty_result() for name, dim in self.dimensions.items()}

        # Try to update repository metadata. This can fail if there is a network error, the URL is
        # invalid, or the repo type is not supported, in which case we do not continue with collection.
        # If it was successful or if there is a temporary error (COLLECTION_GAP) we continue with collection.
        collect, err = await self._update_repo_info(package)
        dependency_info: Dict = {}

        if collect:
            # Collect all 3 dimensions in parallel
            *collected, dependency_info = await asyncio.gather(
                *(dim.collect(package) for dim in self.dimensions.values()),
                self.collect_dependency_info(package),
                return_exceptions=True,
            )

            if isinstance(dependency_info, Exception):
                logger.error(f"Dependency info error: {dependency_info}")
                dependency_info = {}

            for name, result in zip(self.dimensions, collected):
                if isinstance(result, Exception):
                    logger.error(f"{name.capitalize()} dimension error: {result!r}")
                else:
                    results[name] = result
        else:
            # Loud on purpose: a repository that has been renamed or deleted
            # out from under the catalog is the F13 symptom this preflight
            # exists to surface, and it needs a human to fix the entry.
            logger.error(f"Skipping collection for {package['name']}: {err}")

        # Calculate overall score (weighted average of 3 dimensions)
        overall_score = self._calculate_overall_score(
            results["impact"],
            results["ecosystem"],
            results["quality"],
        )

        return {
            "overall_score": overall_score,
            "dimensions": results,
            "dependency_info": dependency_info,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    def _metric_weights(self) -> Dict[str, float]:
        """Dimension weights for the overall score, from config or defaults."""
        weights = self.config.get("metric_weights", {})
        return {
            "impact": weights.get("impact", 0.33),
            "ecosystem": weights.get("ecosystem", 0.34),
            "quality": weights.get("quality", 0.33),
        }

    def _calculate_overall_score(
        self,
        impact: Dict,
        ecosystem: Dict,
        quality: Dict,
    ) -> int:
        """Calculate weighted overall sustainability score based on 3 dimensions

        Default weights (can be configured):
        - Impact: 33%
        - Ecosystem: 34%
        - Quality: 33%
        """
        weights = self._metric_weights()
        impact_weight = weights["impact"]
        ecosystem_weight = weights["ecosystem"]
        quality_weight = weights["quality"]

        # Extract scores from dimension results
        impact_score = impact.get("score", 0)
        ecosystem_score = ecosystem.get("score", 0)
        quality_score = quality.get("score", 0)

        # Calculate weighted average
        overall = (
            impact_score * impact_weight
            + ecosystem_score * ecosystem_weight
            + quality_score * quality_weight
        )

        return int(round(overall))

    async def process_all_software(
        self,
        catalog: Dict,
        software_list: List[str],
    ) -> Dict:
        """Process all software packages

        Args:
            catalog: Software catalog
            software_list: Names of the packages to process, keys of catalog

        Returns:
            Dictionary of all metrics keyed by repository name
        """

        total = len(software_list)
        if total == 0:
            return {}

        all_metrics = {}
        # Repositories named anywhere in the catalog, not just this batch,
        # so a package's dependents don't depend on which batch it is in.
        self.spack_data = await self.collect_spack_dependencies(
            [catalog[p] for p in software_list], self._spack_repos(catalog.values()))

        # Packages are independent, and each one spends nearly all its time
        # waiting on the GitHub API, so they are processed in a small pool
        # rather than strictly one after another. Sequentially this run took
        # 55 minutes for 67 packages against a 120-minute job timeout, and the
        # per-package request count has since roughly doubled.
        #
        # The pool is kept small on purpose: GitHub's secondary rate limits
        # respond to concurrency, not just total volume, so a handful of
        # in-flight packages is the useful range. The old inter-package delay
        # is applied per worker as a stagger.
        concurrency = int(
            self.config.get("rate_limiting", {}).get("concurrent_packages", 2)
        )
        delay = self.config.get("rate_limiting", {}).get("delay_between_packages", 2)
        semaphore = asyncio.Semaphore(max(1, concurrency))
        completed = 0

        async def process(package: Dict) -> None:
            nonlocal completed
            async with semaphore:
                try:
                    metrics = await self.collect_all_metrics(package)
                    all_metrics[package["name"]] = metrics
                except Exception as e:
                    logger.error(f"Failed to process {package['name']}: {e}")
                finally:
                    completed += 1
                    logger.info(f"Processed {completed}/{total}: {package['name']}")
                # Stagger the next acquisition rather than the whole run.
                if delay:
                    await asyncio.sleep(delay)

        await asyncio.gather(*[process(catalog[p]) for p in software_list])

        return all_metrics

    def read_summary(self, summary_file: Path) -> Dict:
        try:
            with open(summary_file, "r") as f:
                return json.load(f)
        except Exception:
            return {}

    def write_summary(self, summary_file: Path, metrics: Dict, all_packages: Dict):
        """Write summary report. This overwrites an existing summary report with the updated version."""

        # Update all packages that have been processed
        for k, v in metrics.items():
            all_packages[k] = v["overall_score"]

        summary_report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_packages": len(metrics),
            "avg_overall_score": sum(m["overall_score"] for m in metrics.values())
            / len(metrics)
            if metrics
            else 0,
            "packages_by_score": {
                "excellent (80-100)": sum(
                    1 for m in metrics.values() if m["overall_score"] >= 80
                ),
                "good (60-79)": sum(
                    1 for m in metrics.values() if 60 <= m["overall_score"] < 80
                ),
                "fair (40-59)": sum(
                    1 for m in metrics.values() if 40 <= m["overall_score"] < 60
                ),
                "needs_improvement (0-39)": sum(
                    1 for m in metrics.values() if m["overall_score"] < 40
                ),
            },
            "top_packages": sorted(
                [(k, v["overall_score"]) for k, v in metrics.items()],
                key=lambda x: x[1],
                reverse=True,
            )[:10],
            "total_packages_overall": len(all_packages),
            "avg_overall_score_overall": sum(v for v in all_packages.values()) / len(
                all_packages) if all_packages else 0,
            "packages_by_score_overall": {
                "excellent (80-100)": sum(
                    1 for v in all_packages.values() if v >= 80
                ),
                "good (60-79)": sum(
                    1 for v in all_packages.values() if 60 <= v < 80
                ),
                "fair (40-59)": sum(
                    1 for v in all_packages.values() if 40 <= v < 60
                ),
                "needs_improvement (0-39)": sum(
                    1 for v in all_packages.values() if v < 40
                ),
            },
            "top_packages_overall": sorted(
                [(k, v) for k, v in all_packages.items()],
                key=lambda x: x[1],
                reverse=True,
            )[:10],
            "all_packages": all_packages
        }

        with open(summary_file, "w") as f:
            json.dump(summary_report, f, indent=2)

        logger.info(f"Summary report written to {summary_file}")

    @staticmethod
    def _output_dir_name(package_name: str, package: Dict) -> str:
        """Repository name the dashboard keys a package's files by, e.g.
        "hdf5" for https://github.com/HDFGroup/hdf5. The catalog's `name:`
        is free text ("HDF5", "CORSA Metrics Framework"), so it can't be used
        as a path; fall back to it only when there is no repo_url.
        """
        path = urlparse(package.get("repo_url") or "").path.strip("/")
        source = path or package_name
        repo = source.split("/")[-1]
        if repo.endswith(".git"):
            repo = repo[:-4]
        return repo

    def _raw_metrics(self, metrics: Dict) -> Dict:
        """metrics.json's `metrics`: the overall score and each dimension's
        measurements (see DimensionCollector.raw_metrics)."""
        dims = metrics.get("dimensions", {})
        return {
            "overall_score": metrics.get("overall_score"),
            **{
                name: dim.raw_metrics(dims.get(name) or dim.empty_result())
                for name, dim in self.dimensions.items()
            },
        }

    def write_metrics(self, output_path: Path, catalog: Dict, all_metrics: Dict):
        """Write per-package metrics.json files for the dashboard.

        Creates: {output_path}/{repo-name}-metrics/metrics.json for each package,
        and beside it report.html -- the same results as one readable page
        with the thresholds each row was judged against (see
        package_report.py). metrics_data's collect workflow commits these to
        data/YYYYMMDD/, and the dashboard's update workflow copies them into
        explore/github-data/.
        """
        failed = []
        written: Dict[str, str] = {}

        for package_name, metrics in all_metrics.items():

            # One package's rendering error used to abort the loop and drop
            # every later package's output; write the rest, then fail the run.
            try:
                overrides = catalog[package_name].get("overrides", {})
                section_data = self.formatter.format(metrics, overrides)
                dashboard_data = {
                    "package": package_name,
                    "metadata": catalog[package_name],
                    "config_exclusions": section_data["config_exclusions"],
                    # The sections the dashboard renders (js/catalog.js).
                    "formatted": {dim: section_data[dim] for dim in self.dimensions},
                    # The dashboard's "last updated" (js/catalog.js).
                    "generated_at": metrics.get("last_updated"),
                    # `dependencies` and `spack`, when collected.
                    **metrics.get("dependency_info", {}),
                    # The measurements the scores and sections came from.
                    "metrics": self._raw_metrics(metrics),
                }
                # Serialized here, before the file is opened, so a value
                # JSON can't hold fails this package alone and leaves no
                # half-written file. allow_nan=False: browsers' JSON.parse
                # rejects the NaN/Infinity json.dumps would otherwise write.
                dashboard_json = json.dumps(dashboard_data, indent=2, allow_nan=False)
            except Exception as e:
                logger.error(f"Could not render dashboard output for {package_name}: {e!r}", exc_info=True)
                failed.append(package_name)
                continue

            # The dashboard keys files by repository name alone, so two
            # catalog entries for same-named repos overwrite each other.
            dir_name = self._output_dir_name(package_name, catalog[package_name])
            if dir_name.lower() in written:
                logger.error(
                    f"{package_name} and {written[dir_name.lower()]} both write "
                    f"{dir_name}-metrics/metrics.json; the dashboard shows only {package_name}"
                )
            written[dir_name.lower()] = package_name
            metrics_dir = output_path / f"{dir_name}-metrics"
            metrics_dir.mkdir(parents=True, exist_ok=True)

            output_file = metrics_dir / "metrics.json"
            output_file.write_text(dashboard_json)

            logger.info(f"Dashboard metrics written to {output_file}")

            report_file = metrics_dir / "report.html"
            report_file.write_text(
                render_package_report(
                    package_name,
                    section_data,
                    metrics,
                    self._metric_weights(),
                    catalog[package_name].get("overrides", {})
                )
            )

        if failed:
            raise RuntimeError(f"Dashboard output failed for: {', '.join(failed)}")


async def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Orchestrate metrics collection for dashboard"
    )
    parser.add_argument(
        "--config",
        default="config/orchestrator.yaml",
        help="Path to configuration file",
    )
    parser.add_argument(
        "--software", help="Filter to specific software (e.g., HDF5)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Perform dry run without writing outputs"
    )
    parser.add_argument(
        "--group", type=int, default=None,
        help="0-indexed round-robin slice of the catalog to process (requires --group-count)",
    )
    parser.add_argument(
        "--group-count", type=int, default=None,
        help="Split the catalog into this many slices; used with --group",
    )
    parser.add_argument(
        "--max-packages", type=int, default=None,
        help="Stop processing after this many packages",
    )
    parser.add_argument(
        "--output-path", type=str, default=None,
        help="Path to output directory (overrides output_path in the configuration file)",
    )
    parser.add_argument(
        "--repo", type=str, default=None,
        help="URL of a single repository to scan instead of the catalog",
    )
    parser.add_argument(
        "--repo-type", type=str, default=None,
        choices=["github", "gitlab"],
        help="Type of repository to scan; required with --repo",
    )
    parser.add_argument(
        "--name", type=str, default=None,
        help="Name to record the --repo results under; required with --repo",
    )
    parser.add_argument(
        "--use-summary-as-filter", action="store_true",
        help="Use the summary file to filter which packages to process. Packages listed in the summary "
             "will be excluded from the results. Can be combined with the --max-packages option to restrict "
             "how many packages to process in one run. If no summary file exists, a new one will be created, "
             "otherwise the summary file will be updated with the new packages."
    )

    args = parser.parse_args()

    if args.repo:
        if not (args.repo_type and args.name):
            logger.error("--repo-type and --name must be specified when using --repo")
            sys.exit(1)

        if args.use_summary_as_filter:
            logger.error("--repo and --use-summary-as-filter cannot be used at the same time")
            sys.exit(1)

    try:
        orchestrator = MetricsOrchestrator(args.config)

        output_path = Path(
            args.output_path or orchestrator.config.get("output_path") or "./output"
        )

        if not args.dry_run:
            output_path.mkdir(parents=True, exist_ok=True)

        summary_file = output_path / "orchestrator_summary.json"

        processed = {}
        if args.use_summary_as_filter:
            summary = orchestrator.read_summary(summary_file)
            processed = summary.get("all_packages", {})

        catalog, software_list = await orchestrator.prepare_software_list(
            filter_software=args.software,
            group=args.group,
            group_count=args.group_count,
            max_packages=args.max_packages,
            repo=args.repo,
            repo_type=args.repo_type,
            name=args.name,
            processed_packages=processed,
        )

        metrics = await orchestrator.process_all_software(catalog, software_list)

        if not args.dry_run and metrics:
            orchestrator.write_metrics(output_path, catalog, metrics)
            orchestrator.write_summary(summary_file, metrics, processed)

        logger.info(f"\n{'='*60}")
        logger.info("Orchestration complete!")
        logger.info(f"Processed {len(metrics)} packages")
        logger.info(f"{'='*60}\n")

    except Exception as e:
        logger.error(f"Orchestration failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
