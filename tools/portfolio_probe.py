#!/usr/bin/env python3
"""
Portfolio Probe (CASS metrics framework)

Tests the framework's own heuristics against the real state of every
tracked repository, instead of waiting for a maintainer to file an issue
about a false negative -- see METRIC_BLIND_SPOTS.md for the analysis this
tool grew out of.

Reuses the actual collector code and constants (RepoTree,
ReproducibilityCollector._check_semantic_versioning, _MAX_ANALYSIS_WORKFLOWS)
rather than a separate copy of the same logic, so this stays in sync
automatically as those evolve instead of silently drifting into its own,
eventually-wrong idea of what the collectors do.

Checks, in order:

1. Catalog identity (hard gate -- exit 1 on any failure). A catalog entry
   pointing at a renamed, deleted, or mistranscribed repository means every
   collector's own 404s read as a confirmed absence per file, not as "wrong
   repository entirely" (METRIC_BLIND_SPOTS.md class F13).
2. CI-workflow read coverage (report only). A repo with more workflows than
   reliability.py's cap allows reading gets a partial scan, not a confident
   absence -- but if that starts applying to a large share of the
   portfolio, the cap itself needs revisiting (class F4).
3. Version-scheme coverage (report only). A repo whose sampled tags don't
   resolve to any recognized scheme is either genuinely undisciplined about
   tagging, or evidence of one more naming convention _versioning_scheme
   doesn't know about yet (class F6) -- this is a worklist, not a pass/fail
   bar, since some repos will always be genuine exceptions.

Usage:
    python tools/portfolio_probe.py [--config config/orchestrator.yaml]
"""

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator import MetricsOrchestrator  # noqa: E402
from collectors.ecosystem.base import RepoTree, COLLECTION_GAP  # noqa: E402
from collectors.quality.reliability import _MAX_ANALYSIS_WORKFLOWS  # noqa: E402
from collectors.quality.reproducibility import ReproducibilityCollector  # noqa: E402

_CONCURRENCY = 6


async def _probe_one(
    client: httpx.AsyncClient, headers: Dict[str, str], sem: asyncio.Semaphore,
    orchestrator: MetricsOrchestrator, repro: ReproducibilityCollector, repo_name: str,
) -> Dict[str, Any]:
    async with sem:
        result: Dict[str, Any] = {"repo": repo_name}

        exists = await orchestrator._confirm_repo_exists(repo_name)
        result["exists"] = exists
        if not exists:
            return result

        owner, repo = repo_name.split("/", 1)
        tree = await RepoTree.fetch(client, headers, owner, repo)
        if tree is COLLECTION_GAP:
            result["tree_gap"] = True
            return result

        workflows = tree.find(r"^\.github/workflows/.*\.ya?ml$")
        result["workflow_count"] = len(workflows)
        result["workflow_scan_partial"] = len(workflows) > _MAX_ANALYSIS_WORKFLOWS

        # The real collector method, not a separate reimplementation --
        # keeps this in sync as that logic evolves instead of drifting.
        version_result = await repro._check_semantic_versioning(client, owner, repo)
        if not version_result.get("not_collected"):
            result["version_scheme_found"] = version_result["uses_semver"]
            result["tags_sampled"] = version_result.get("example_tags")

        return result


async def main(config_path: str) -> int:
    orchestrator = MetricsOrchestrator(config_path=config_path)
    catalog = orchestrator.load_software_catalog()
    repo_names = sorted(catalog.keys())
    if not repo_names:
        print("Catalog is empty or could not be fetched -- nothing to probe.")
        return 0

    token = orchestrator._get_github_token()
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    repro = ReproducibilityCollector(github_token=token)

    sem = asyncio.Semaphore(_CONCURRENCY)
    async with httpx.AsyncClient(timeout=30.0) as client:
        results = await asyncio.gather(
            *[_probe_one(client, headers, sem, orchestrator, repro, r) for r in repo_names]
        )

    return _report(results)


def _report(results: List[Dict[str, Any]]) -> int:
    """Classify probe results and print the summary. Returns the process
    exit code (1 only for the hard gate: an unresolved catalog entry).
    Split out from main() so the classification logic is unit-testable
    without a live network call.
    """
    missing = [r for r in results if not r["exists"]]
    gapped = [r for r in results if r.get("tree_gap")]
    partial_scans = [r for r in results if r.get("workflow_scan_partial")]
    no_scheme = [
        r for r in results
        if "version_scheme_found" in r and not r["version_scheme_found"]
    ]

    print(f"Probed {len(results)} catalog entries.\n")

    if gapped:
        print(f"COULD NOT FETCH (transient -- rerun): {len(gapped)}")
        for r in gapped:
            print(f"  {r['repo']}")
        print()

    if partial_scans:
        print(f"CI-WORKFLOW SCAN PARTIAL (more workflows than the read cap): {len(partial_scans)}")
        for r in sorted(partial_scans, key=lambda r: -r["workflow_count"]):
            print(f"  {r['repo']:45s} {r['workflow_count']} workflows (cap {_MAX_ANALYSIS_WORKFLOWS})")
        print()

    if no_scheme:
        print(f"NO RECOGNIZED VERSION SCHEME in sampled tags: {len(no_scheme)}")
        for r in no_scheme:
            print(f"  {r['repo']:45s} {r.get('tags_sampled')}")
        print()

    if missing:
        print(f"CATALOG ENTRIES THAT DO NOT RESOLVE ON GITHUB: {len(missing)}")
        for r in missing:
            print(f"  {r['repo']}")
        print(
            "\nThese repositories 404 -- the catalog entry is stale (renamed, "
            "deleted, or mistranscribed). The pipeline already skips them "
            "safely (orchestrator.py's _confirm_repo_exists), but the entries "
            "themselves need correcting at the source "
            "(dashboard/explore/github-data/intReposInfo.json)."
        )
        return 1

    print("All catalog entries resolve. No hard failures.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/orchestrator.yaml")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.config)))
