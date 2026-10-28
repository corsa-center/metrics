#!/usr/bin/env python3
"""
Dashboard data generator (CORSA metrics framework)

Builds the dashboard's intReposInfo.json, intRepos_Topics.json,
intRepos_Dependencies.json, dependencyInfo.json,
spackPackageDependencies.json and spackPackageDependents.json from the
per-package metrics.json files the orchestrator commits to the
metrics_data repository (data/YYYYMMDD/<repo>-metrics/metrics.json). No API
calls: everything comes from the `metadata`, `dependencies` and `spack` the collector already wrote. All the
dashboard-specific shaping lives here, so the metrics files stay neutral
about which frontend reads them.

Collection runs in batches, so one day's directory may not hold every
package. For each package the newest metrics.json within --max-age-days is
used, and each collected key (dependencies, spack, and the counts in
metadata) falls back to an older file if the newest one recorded a
collection gap for it.

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


# Metadata the collector's repository lookup fills in (forge.base's
# NULLABLE_METADATA_KEYS and DEFINITE_METADATA_KEYS). created_at is set
# whenever that lookup succeeded, so a file without it recorded a gap.
_REPO_KEYS = (
    "description", "homepage", "language", "created_at", "updated_at", "pushed_at",
    "default_branch", "license", "owner_avatar_url", "parent",
    "archived", "disabled", "size_kb", "topics", "stars", "forks",
)
# Metadata keys the collector leaves out when their lookup was a gap.
_COUNT_KEYS = ("stars", "forks", "pull_requests", "issues", "mentionable_users")
# Top-level metrics.json keys likewise left out on a gap.
_COLLECTED_KEYS = ("dependencies", "spack")


def load_packages(data_dir: Path, max_age_days: int = 7, today: Optional[datetime] = None) -> Dict[str, Dict]:
    """Newest data per package, keyed by the repository name its
    <repo>-metrics directory is named after.

    Each value has `metadata` and, when any file in the window had them,
    `dependencies` and `spack` -- each from the newest file that recorded
    it. If the newest file's repository lookup was a gap, the repository
    fields come from the newest file where it succeeded; counts missing
    from the newest metadata are filled in the same way.
    """
    today = today or datetime.now(timezone.utc)
    packages: Dict[str, Dict] = {}
    repo_found = set()
    for name, metrics_file in _metrics_files(_date_dirs(data_dir, max_age_days, today)):
        try:
            metrics = json.loads(metrics_file.read_text())
        except (OSError, ValueError) as e:
            print(f"Warning: skipping unreadable {metrics_file}: {e}", file=sys.stderr)
            continue
        metadata = metrics.get("metadata") or {}
        if name not in packages:
            packages[name] = {"metadata": dict(metadata)}
        package = packages[name]
        if name not in repo_found and "created_at" in metadata:
            package["metadata"].update((k, metadata[k]) for k in _REPO_KEYS if k in metadata)
            repo_found.add(name)
        for key in _COUNT_KEYS:
            if key not in package["metadata"] and key in metadata:
                package["metadata"][key] = metadata[key]
        for key in _COLLECTED_KEYS:
            if key not in package and key in metrics:
                package[key] = metrics[key]
    return packages


def _total(count: Optional[int]) -> Dict:
    return {"totalCount": count}


def _license_info(license_data: Optional[Dict]) -> Optional[Dict]:
    """GitHub GraphQL's licenseInfo from the metadata's license."""
    if not license_data:
        return None
    key = license_data.get("key")
    return {
        "name": license_data.get("name"),
        "spdxId": license_data.get("spdx_id"),
        # Where GitHub's licenseInfo.url points.
        "url": f"http://choosealicense.com/licenses/{key}/" if key else None,
    }


def repo_entry(package: Dict) -> Optional[Dict]:
    """One intReposInfo.json entry, in the GitHub GraphQL shape the
    dashboard's JavaScript reads."""
    metadata = package["metadata"]
    key = _repo_key(metadata)
    if not key:
        return None
    owner, _, name = key.rpartition("/")
    language = metadata.get("language")
    parent = metadata.get("parent")
    pull_requests = metadata.get("pull_requests") or {}
    issues = metadata.get("issues") or {}
    entry = {
        "nameWithOwner": key,
        "name": name,
        "owner": {"login": owner, "avatarUrl": metadata.get("owner_avatar_url")},
        "url": metadata["repo_url"].rstrip("/").removesuffix(".git"),
        "homepageUrl": metadata.get("homepage"),
        "description": metadata.get("description"),
        "createdAt": metadata.get("created_at"),
        "defaultBranchRef": {"name": metadata.get("default_branch")},
        "licenseInfo": _license_info(metadata.get("license")),
        "primaryLanguage": {"name": language} if language else None,
        "repositoryTopics": _total(len(metadata.get("topics") or [])),
        "parent": {
            "nameWithOwner": parent,
            "name": parent.rpartition("/")[2],
            "owner": {"login": parent.rpartition("/")[0]},
        } if parent else None,
        "stargazers": _total(metadata.get("stars")),
        "forks": _total(metadata.get("forks")),
        "mentionableUsers": _total(metadata.get("mentionable_users")),
        "pullRequests_Open": _total(pull_requests.get("open")),
        "pullRequests_Closed": _total(pull_requests.get("closed")),
        "pullRequests_Merged": _total(pull_requests.get("merged")),
        "issues_Open": _total(issues.get("open")),
        "issues_Closed": _total(issues.get("closed")),
        # Unpublished packages stay in the data; the dashboard decides how
        # to show them.
        "published": metadata.get("published", True),
    }
    if metadata.get("cdash"):
        entry["cdash"] = metadata["cdash"]
    return entry


def build_repos_info(packages: Dict[str, Dict]) -> Dict:
    """intReposInfo.json: {"data": {nameWithOwner: GraphQL-shaped repo}}."""
    data = {}
    for name, package in sorted(packages.items()):
        entry = repo_entry(package)
        if entry is None:
            print(f"Warning: no repo_url for {name}; left out of intReposInfo.json", file=sys.stderr)
            continue
        data[entry["nameWithOwner"]] = entry
    return {"data": data}


def build_topics(packages: Dict[str, Dict]) -> Dict:
    """intRepos_Topics.json: {"data": {nameWithOwner: {repositoryTopics}}},
    in the GitHub GraphQL shape the catalog reads."""
    data = {}
    for _, package in sorted(packages.items()):
        key = _repo_key(package["metadata"])
        if not key:
            continue
        topics = package["metadata"].get("topics") or []
        data[key] = {"repositoryTopics": {
            "totalCount": len(topics),
            "nodes": [{"topic": {"name": topic}} for topic in topics],
        }}
    return {"data": data}


def build_dependencies(packages: Dict[str, Dict]) -> Dict:
    """intRepos_Dependencies.json: {"data": {nameWithOwner: dependency graph}}."""
    data = {}
    for _, package in sorted(packages.items()):
        dependencies = package.get("dependencies")
        key = _repo_key(package["metadata"])
        if dependencies and key:
            data[key] = {k: v for k, v in dependencies.items() if k != "repositories"}
    return {"data": data}


def build_dependency_info(packages: Dict[str, Dict]) -> Dict:
    """dependencyInfo.json: {"data": {nameWithOwner: {nameWithOwner, name,
    languages, owner}}} for every repository a dependency graph points at.

    The dashboard's dependency graph looks up each of those repositories
    here without checking, so one the collector couldn't describe gets an
    entry with no language rather than none at all.
    """
    data = {}
    for _, package in sorted(packages.items()):
        dependencies = package.get("dependencies") or {}
        repositories = dependencies.get("repositories") or {}
        for manifest in (dependencies.get("dependencyGraphManifests") or {}).get("nodes") or []:
            for node in (manifest.get("dependencies") or {}).get("nodes") or []:
                key = (node.get("repository") or {}).get("nameWithOwner")
                if not key:
                    continue
                if key in repositories:
                    data[key] = repositories[key]
                else:
                    owner, _, name = key.partition("/")
                    data.setdefault(key, {
                        "nameWithOwner": key,
                        "name": name,
                        "languages": {"nodes": []},
                        "owner": {"login": owner, "isVerified": None},
                    })
    return {"data": data}


def build_spack(packages: Dict[str, Dict]) -> Tuple[Dict, Dict]:
    """spackPackageDependencies.json and spackPackageDependents.json, both
    keyed by Spack package name."""
    dependencies, dependents = {}, {}
    for _, package in sorted(packages.items()):
        spack = package.get("spack")
        if not spack:
            continue
        if spack.get("dependencies"):
            dependencies[spack["name"]] = spack["dependencies"]
        if spack.get("dependents"):
            dependents[spack["name"]] = spack["dependents"]
    return dependencies, dependents


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
        # Publishing an empty catalog would blank the whole dashboard.
        print("No package has a repo_url; refusing to write an empty intReposInfo.json", file=sys.stderr)
        return 1

    spack_dependencies, spack_dependents = build_spack(packages)
    args.output.mkdir(parents=True, exist_ok=True)
    write_json(args.output / "intReposInfo.json", repos_info)
    write_json(args.output / "intRepos_Topics.json", build_topics(packages))
    write_json(args.output / "intRepos_Dependencies.json", build_dependencies(packages))
    write_json(args.output / "dependencyInfo.json", build_dependency_info(packages))
    write_json(args.output / "spackPackageDependencies.json", spack_dependencies)
    write_json(args.output / "spackPackageDependents.json", spack_dependents)
    print(f"Wrote {len(repos_info['data'])} repositories to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
