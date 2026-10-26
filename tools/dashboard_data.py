#!/usr/bin/env python3
"""
Dashboard data generator (CASS metrics framework)

Builds the dashboard's intReposInfo.json and intRepos_Dependencies.json from
the per-package metrics.json files the orchestrator commits to the
metrics_data repository (data/YYYYMMDD/<repo>-metrics/metrics.json). No API
calls: everything comes from the `repo_info`, `dependencies`, `stars` and
`metadata` the collector already wrote.

Collection runs in batches, so one day's directory may not hold every
package. For each package the newest metrics.json within --max-age-days is
used, and each key (repo_info, dependencies) falls back to an older file if
the newest one recorded a collection gap for it.

spackPackageDependencies.json and spackPackageDependents.json need Spack
itself -- see tools/spack_dependencies.py.

Stdlib only, so it runs without the collector's requirements.

Usage:
    python tools/dashboard_data.py --data-dir metrics_data/data \\
        --output metrics_data/data/20261026
"""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple
from urllib.parse import urlparse

_DATE_DIR = re.compile(r"^\d{8}$")


def _date_dirs(data_dir: Path, max_age_days: int, today: datetime) -> List[Path]:
    """YYYYMMDD subdirectories no older than max_age_days, newest first."""
    cutoff = (today - timedelta(days=max_age_days)).strftime("%Y%m%d")
    dirs = [d for d in data_dir.iterdir() if d.is_dir() and _DATE_DIR.match(d.name) and d.name >= cutoff]
    return sorted(dirs, key=lambda d: d.name, reverse=True)


def _metrics_files(date_dirs: List[Path]) -> Iterator[Tuple[str, Path]]:
    """(repository name, metrics.json) pairs, newest day first."""
    for date_dir in date_dirs:
        for metrics_file in sorted(date_dir.glob("*-metrics/metrics.json")):
            yield metrics_file.parent.name[:-len("-metrics")], metrics_file


def _repo_key(metadata: Dict) -> Optional[str]:
    """owner/repo (or group/subgroup/repo) from the catalog's repo_url."""
    path = urlparse(metadata.get("repo_url") or "").path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return path or None


def load_packages(data_dir: Path, max_age_days: int = 7, today: Optional[datetime] = None) -> Dict[str, Dict]:
    """Newest data per published package, keyed by the repository name its
    <repo>-metrics directory is named after.

    Each value has `metadata`, `stars` and, when any file in the window had
    them, `repo_info` and `dependencies` -- each from the newest file that
    recorded it.
    """
    today = today or datetime.now(timezone.utc)
    packages: Dict[str, Dict] = {}
    for name, metrics_file in _metrics_files(_date_dirs(data_dir, max_age_days, today)):
        try:
            metrics = json.loads(metrics_file.read_text())
        except (OSError, ValueError) as e:
            print(f"Warning: skipping unreadable {metrics_file}: {e}", file=sys.stderr)
            continue
        if name not in packages:
            metadata = metrics.get("metadata") or {}
            if not metadata.get("published", True):
                packages[name] = {"skip": True}
                continue
            packages[name] = {"metadata": metadata, "stars": metrics.get("stars", 0)}
        package = packages[name]
        if package.get("skip"):
            continue
        for key in ("repo_info", "dependencies"):
            if key not in package and key in metrics:
                package[key] = metrics[key]
    return {name: p for name, p in packages.items() if not p.get("skip")}


def build_repos_info(packages: Dict[str, Dict]) -> Dict:
    """intReposInfo.json: {"data": {nameWithOwner: GraphQL-shaped repo}}."""
    data = {}
    for name, package in sorted(packages.items()):
        repo_info = package.get("repo_info")
        if not repo_info:
            print(f"Warning: no repo_info for {name}; left out of intReposInfo.json", file=sys.stderr)
            continue
        entry = dict(repo_info)
        entry["stargazers"] = {"totalCount": package.get("stars", 0)}
        cdash = package["metadata"].get("cdash")
        if cdash:
            entry["cdash"] = cdash
        data[entry.get("nameWithOwner") or _repo_key(package["metadata"])] = entry
    return {"data": data}


def build_dependencies(packages: Dict[str, Dict]) -> Dict:
    """intRepos_Dependencies.json: {"data": {nameWithOwner: dependency graph}}."""
    data = {}
    for name, package in sorted(packages.items()):
        dependencies = package.get("dependencies")
        if not dependencies:
            continue
        key = (package.get("repo_info") or {}).get("nameWithOwner") or _repo_key(package["metadata"])
        data[key] = dependencies
    return {"data": data}


def write_json(path: Path, obj: Dict) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1].strip())
    parser.add_argument("--data-dir", type=Path, required=True,
                        help="metrics_data's data/ directory (holds the YYYYMMDD directories)")
    parser.add_argument("--output", type=Path, required=True,
                        help="directory to write the dashboard files into")
    parser.add_argument("--max-age-days", type=int, default=7,
                        help="ignore metrics older than this many days (default: 7)")
    args = parser.parse_args(argv)

    packages = load_packages(args.data_dir, args.max_age_days)
    if not packages:
        print(f"No metrics found under {args.data_dir} in the last {args.max_age_days} days", file=sys.stderr)
        return 1

    repos_info = build_repos_info(packages)
    if not repos_info["data"]:
        # Before the collector started writing repo_info, or after a run of
        # nothing but gaps -- publishing an empty catalog would blank the
        # whole dashboard.
        print("No package has repo_info; refusing to write an empty intReposInfo.json", file=sys.stderr)
        return 1

    args.output.mkdir(parents=True, exist_ok=True)
    write_json(args.output / "intReposInfo.json", repos_info)
    write_json(args.output / "intRepos_Dependencies.json", build_dependencies(packages))
    print(f"Wrote {len(repos_info['data'])} repositories to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
