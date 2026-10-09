"""
CI/CD Metrics Collector (CASS Report Section 4.3.2 — Development Practices)

Collects and aggregates CI/CD health metrics from GitHub:
- Workflow execution time (average over last N runs)
- Workflow success rate (per-workflow and overall)
- Deployment frequency (successful deployments in a time window)
- Release frequency (GitHub releases in a time window)
- Average time to failure (failed workflow run duration)
- Average cycle time (PR open → merge)

Scoring follows DORA metrics guidance:
  https://docs.gitlab.com/user/analytics/dora_metrics/
"""

import asyncio
import httpx
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from forge.base import COLLECTION_GAP, RetryingTransport
from forge.interface import Forge
from collectors.ecosystem.base import get_threshold

logger = logging.getLogger(__name__)

_GITHUB_DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


class CICDMetricsCollector:
    """Collects CI/CD development-practice metrics from GitHub (Section 4.3.2)."""

    def __init__(self, forge: Forge):
        self.forge = forge

    # ------------------------------------------------------------------ #
    # Public interface                                                     #
    # ------------------------------------------------------------------ #

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        """Collect all CI/CD metrics for a package and return a scored result."""
        repo_url = package.get("repo_url", "")
        ref = self.forge.extract_ref(repo_url)
        if not ref:
            # Matches the previous _parse_repo_url's fail-fast contract: no
            # _empty_result convention here, orchestrator.py's generic
            # except-and-log around collector.collect() handles it.
            raise ValueError(f"Invalid GitHub URL format: {repo_url}")

        logger.info(f"Beginning CI/CD metric collection for {package.get('name')}")

        async with httpx.AsyncClient(transport=RetryingTransport()) as client:
            branch = package.get("repo_branch") or await self._get_default_branch(client, ref)
            (
                exec_time,
                workflow_success,
                deployments,
                releases,
                time_to_failure,
                cycle_time,
            ) = await asyncio.gather(
                self.workflow_execution_time(client, ref, branch),
                self.percentage_workflow_success(client, ref),
                self.deployment_frequency(client, ref),
                self.release_frequency(client, ref),
                self.average_time_failure(client, ref, branch),
                self.average_cycle_time(client, ref),
            )

        results: Dict[str, Any] = {}
        results.update(exec_time)
        results.update(workflow_success)
        results.update(deployments)
        results.update(releases)
        results.update(time_to_failure)
        results.update(cycle_time)
        return self._calculate_score(results)

    # ------------------------------------------------------------------ #
    # Scoring                                                              #
    # ------------------------------------------------------------------ #

    def _calculate_score(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """Score results against DORA elite/high thresholds (0–6 points).

        References:
          https://docs.gitlab.com/user/analytics/dora_metrics/
          https://docs.gitlab.com/user/analytics/value_streams_dashboard/
        """
        score = 0
        max_score = 6

        # Workflow execution time under the cap (0 means no runs collected — skip)
        max_exec_hours = get_threshold("4.3.2", "CI/CD Effectiveness Assessment", "max_execution_time_hours")
        exec_time = results.get("average_workflow_execution_time")
        if exec_time is not None and exec_time > 0 and (exec_time / 3600) < max_exec_hours:
            score += 1

        # Overall workflow success above the configured rate
        min_success_pct = get_threshold("4.3.2", "CI/CD Effectiveness Assessment", "min_success_rate_pct")
        if results.get("total_workflow_success_percentage", 0) > min_success_pct:
            score += 1

        # Deployment frequency: at least the configured minimum per year
        min_deployments_per_year = get_threshold("4.3.2", "CI/CD Effectiveness Assessment", "min_deployments_per_year")
        num_deployments_key = next(
            (k for k in results if "num_of_deployments" in k), None
        )
        if num_deployments_key:
            count = results.get(num_deployments_key)
            if count is None:
                # Repo does not use GitHub deployments — exclude from denominator.
                max_score -= 1
            else:
                num_days = int(re.findall(r"\d+", num_deployments_key)[0])
                # The max(1, ...) floor predates the threshold registry and
                # is intentionally not overridable: a short observation
                # window scaling min_deployments_per_year down to 0 still
                # requires at least one deployment to pass.
                if count >= max(1, int((num_days / 365) * min_deployments_per_year)):
                    score += 1

        # Release frequency: at least the configured minimum per year
        min_releases_per_year = get_threshold("4.3.2", "CI/CD Effectiveness Assessment", "min_releases_per_year")
        num_releases_key = next(
            (k for k in results if "num_of_releases" in k), None
        )
        if num_releases_key:
            count = results.get(num_releases_key)
            if count is None:
                max_score -= 1
            else:
                num_days = int(re.findall(r"\d+", num_releases_key)[0])
                # Same floor as deployment frequency above.
                if count >= max(1, int((num_days / 365) * min_releases_per_year)):
                    score += 1

        # Average time to failure under the cap (0 means no failed runs — skip)
        max_time_to_failure_hours = get_threshold("4.3.2", "CI/CD Effectiveness Assessment", "max_time_to_failure_hours")
        time_to_failure = results.get("average_time_to_failure")
        if time_to_failure is not None and time_to_failure > 0 and (time_to_failure / 3600) < max_time_to_failure_hours:
            score += 1

        # Average cycle time under the configured cap (168h / 1 week by default --
        # see config/thresholds.yaml for why that's the default and not DORA's
        # 24h "elite" tier).
        cycle_time = results.get("average_cycle_time")
        max_cycle_hours = get_threshold("4.3.2", "CI/CD Effectiveness Assessment", "max_cycle_time_hours")
        if cycle_time is not None and cycle_time > 0 and (cycle_time / 3600) < max_cycle_hours:
            score += 1

        safe_max = max(max_score, 1)
        return {
            "score": score,
            "max_score": max_score,
            "percentage": round((score / safe_max) * 100, 2),
            "details": list(results.items()),
        }

    # ------------------------------------------------------------------ #
    # Metric collectors                                                    #
    # ------------------------------------------------------------------ #

    async def workflow_execution_time(
        self, client: httpx.AsyncClient, ref: str, branch: str, num_workflows: int = 100
    ) -> Dict[str, float]:
        """Average workflow execution time (seconds) over the last N runs."""
        workflows = await self._get_last_n_workflow_runs(
            client, ref=ref, num_workflows=num_workflows, branch=branch
        )
        total = sum(
            self._parse_github_datetime_string(w["updated_at"]).timestamp()
            - self._parse_github_datetime_string(w["created_at"]).timestamp()
            for w in workflows
        )
        avg = total / max(len(workflows), 1)
        logger.debug(f"Average workflow execution time: {avg:.1f}s")
        return {"average_workflow_execution_time": avg}

    async def percentage_workflow_success(
        self, client: httpx.AsyncClient, ref: str
    ) -> Dict[str, Any]:
        """Per-workflow and overall success percentage across the last 30 runs."""
        workflows = await self.forge.ci_workflows(client, ref)
        if not workflows:
            if workflows is COLLECTION_GAP:
                logger.error(f"Error fetching workflows for {ref}: collection gap")
            return {"workflow_success_percentage": {}}

        workflow_success_pct: Dict[str, float] = {}
        total_successes = 0
        total_runs = 0

        for w in workflows:
            runs = await self.forge.ci_workflow_runs(client, ref, w["id"])
            if not runs:
                continue
            successes = sum(
                1 for r in runs
                if r.get("status") == "completed" and r.get("conclusion") == "success"
            )
            workflow_success_pct[w["name"]] = successes / len(runs) * 100
            total_successes += successes
            total_runs += len(runs)

        logger.debug(f"Workflow success percentages: {workflow_success_pct}")
        return {
            "workflow_success_percentage": workflow_success_pct,
            "total_workflow_success_percentage": total_successes / max(total_runs, 1) * 100,
        }

    async def deployment_frequency(
        self,
        client: httpx.AsyncClient,
        ref: str,
        days_to_measure: int = 365,
        pages: int = 1,
        page_size: int = 100,
    ) -> Dict[str, Optional[int]]:
        """Number of successful GitHub deployments in the last `days_to_measure` days."""
        key = f"num_of_deployments_last_{days_to_measure}_days"
        deployments: List[Dict] = []
        for page in range(1, pages + 1):
            batch = await self.forge.deployments(client, ref, per_page=page_size, page=page)
            if not batch:
                break
            deployments += batch

        if not deployments:
            # Distinguish "zero deployments in window" from "repo doesn't use GitHub deployments".
            return {key: None}

        start_date = datetime.now(timezone.utc) - timedelta(days=days_to_measure)
        deployment_count = 0
        for d in deployments:
            if self._parse_github_datetime_string(d["created_at"]) < start_date:
                break
            if await self.forge.deployment_succeeded(client, d["statuses_url"]):
                deployment_count += 1

        logger.debug(f"Deployments in last {days_to_measure} days: {deployment_count}")
        return {key: deployment_count}

    async def release_frequency(
        self,
        client: httpx.AsyncClient,
        ref: str,
        days_to_measure: int = 365,
        pages: int = 1,
        page_size: int = 100,
    ) -> Dict[str, Optional[int]]:
        """Number of GitHub releases published in the last `days_to_measure` days."""
        key = f"num_of_releases_last_{days_to_measure}_days"
        releases: List[Dict] = []
        for page in range(1, pages + 1):
            batch = await self.forge.releases(client, ref, per_page=page_size, page=page)
            if not batch:
                break
            releases += batch

        if not releases:
            return {key: None}

        start_date = datetime.now(timezone.utc) - timedelta(days=days_to_measure)
        release_count = 0
        for r in releases:
            if not r.get("published_at"):
                continue
            if self._parse_github_datetime_string(r["published_at"]) >= start_date:
                release_count += 1
            else:
                break

        logger.debug(f"Releases in last {days_to_measure} days: {release_count}")
        return {key: release_count}

    async def average_time_failure(
        self, client: httpx.AsyncClient, ref: str, branch: str, num_workflows: int = 100
    ) -> Dict[str, float]:
        """Average duration (seconds) of failed workflow runs over the last N runs."""
        runs = await self._get_last_n_workflow_runs(
            client, ref, num_workflows, branch, status="failure"
        )
        total = sum(
            self._parse_github_datetime_string(w["updated_at"]).timestamp()
            - self._parse_github_datetime_string(w["created_at"]).timestamp()
            for w in runs
        )
        avg = total / max(len(runs), 1)
        logger.debug(f"Average time to failure: {avg:.1f}s")
        return {"average_time_to_failure": avg}

    async def average_cycle_time(
        self,
        client: httpx.AsyncClient,
        ref: str,
        pages: int = 1,
        page_size: int = 100,
    ) -> Dict[str, float]:
        """Average time (seconds) from PR open to merge across the last N closed PRs."""
        pull_requests: List[Dict] = []
        for page in range(1, pages + 1):
            batch = await self.forge.pull_requests(
                client, ref, state="closed", per_page=page_size, page=page,
            )
            if not batch:
                break
            pull_requests += batch

        total_cycle_time = sum(
            self._parse_github_datetime_string(pr["merged_at"]).timestamp()
            - self._parse_github_datetime_string(pr["created_at"]).timestamp()
            for pr in pull_requests
            if pr.get("merged_at")
        )
        avg = total_cycle_time / max(len(pull_requests), 1)
        logger.debug(f"Average cycle time: {avg:.1f}s")
        return {"average_cycle_time": avg}

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    async def _get_last_n_workflow_runs(
        self,
        client: httpx.AsyncClient,
        ref: str,
        num_workflows: int = 100,
        branch: str = "main",
        status: Optional[str] = None,
    ) -> List[Dict]:
        """Fetch up to `num_workflows` workflow runs, paging as needed."""
        results: List[Dict] = []
        remaining = num_workflows
        page = 1
        while remaining > 0:
            per_page = min(remaining, 100)
            batch = await self.forge.ci_runs(
                client, ref, branch=branch, status=status, per_page=per_page, page=page,
            )
            if not batch:
                break
            results += batch
            if len(batch) < per_page:
                break
            remaining -= len(batch)
            page += 1
        return results

    async def _get_default_branch(self, client: httpx.AsyncClient, ref: str) -> str:
        """The repo's actual default branch, falling back to "main" only if
        it can't be determined.

        Workflow-run queries scoped to a hardcoded "main" silently return
        zero runs for any repo whose default branch is named something else
        -- AMReX-Codes/amrex, for one, works on "development" and has zero
        runs on a branch literally named "main", which read as "no CI data"
        instead of the ~64k real runs it actually has.
        """
        info = await self.forge.repo_info(client, ref)
        if not info:
            logger.error(f"Could not fetch default branch for {ref}; assuming main")
            return "main"
        return info.get("default_branch") or "main"

    def _parse_github_datetime_string(self, date: str) -> datetime:
        """Parse a GitHub ISO 8601 timestamp into a timezone-aware datetime."""
        return datetime.strptime(date, _GITHUB_DATETIME_FORMAT)
