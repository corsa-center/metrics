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

logger = logging.getLogger(__name__)

# Cycle-time "elite" threshold from DORA: less than one day (in hours).
_CYCLE_TIME_ELITE_HOURS = 24
# DORA "elite" deployment frequency: at least once per day → ≥1 per year window.
_MIN_DEPLOYMENTS_PER_YEAR = 1
_MIN_RELEASES_PER_YEAR = 1


class CICDMetricsCollector:
    """Collects CI/CD development-practice metrics from GitHub (Section 4.3.2)."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        credentials = config.get("api_credentials", {})
        # Safe access — token may be absent or empty in dev/test environments.
        self.github_token: str = credentials.get("github", {}).get("token", "") or ""
        self.github_datetime_format = "%Y-%m-%dT%H:%M:%S%z"
        self.headers: Dict[str, str] = {}
        if self.github_token:
            self.headers["Authorization"] = f"token {self.github_token}"
            self.headers["Accept"] = "application/vnd.github.v3+json"

    # ------------------------------------------------------------------ #
    # Public interface                                                     #
    # ------------------------------------------------------------------ #

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        """Collect all CI/CD metrics for a package and return a scored result."""
        repo_url = self._parse_repo_url(package.get("repo_url", ""))
        branch = package.get("repo_branch", "main")

        logger.info(f"Beginning CI/CD metric collection for {package.get('name')}")

        async with httpx.AsyncClient() as client:
            (
                exec_time,
                workflow_success,
                deployments,
                releases,
                time_to_failure,
                cycle_time,
            ) = await asyncio.gather(
                self.workflow_execution_time(client, repo_url, branch),
                self.percentage_workflow_success(client, repo_url),
                self.deployment_frequency(client, repo_url),
                self.release_frequency(client, repo_url),
                self.average_time_failure(client, repo_url, branch),
                self.average_cycle_time(client, repo_url),
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

        # Workflow execution time < 1 hour (0 means no runs collected — skip)
        exec_time = results.get("average_workflow_execution_time")
        if exec_time is not None and exec_time > 0 and (exec_time / 3600) < 1:
            score += 1

        # Overall workflow success > 60%
        if results.get("total_workflow_success_percentage", 0) > 60:
            score += 1

        # Deployment frequency: at least 1 per year
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
                if count >= max(1, int((num_days / 365) * _MIN_DEPLOYMENTS_PER_YEAR)):
                    score += 1

        # Release frequency: at least 1 per year
        num_releases_key = next(
            (k for k in results if "num_of_releases" in k), None
        )
        if num_releases_key:
            count = results.get(num_releases_key)
            if count is None:
                max_score -= 1
            else:
                num_days = int(re.findall(r"\d+", num_releases_key)[0])
                if count >= max(1, int((num_days / 365) * _MIN_RELEASES_PER_YEAR)):
                    score += 1

        # Average time to failure < 1 hour (0 means no failed runs — skip)
        time_to_failure = results.get("average_time_to_failure")
        if time_to_failure is not None and time_to_failure > 0 and (time_to_failure / 3600) < 1:
            score += 1

        # Average cycle time < 1 week (168 hours)
        cycle_time = results.get("average_cycle_time")
        if cycle_time is not None and cycle_time > 0 and (cycle_time / 3600) < (7 * 24):
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
        self, client: httpx.AsyncClient, repo_url: str, branch: str, num_workflows: int = 100
    ) -> Dict[str, float]:
        """Average workflow execution time (seconds) over the last N runs."""
        workflows = await self._get_last_n_workflow_runs(
            client, repo_url=repo_url, num_workflows=num_workflows, branch=branch
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
        self, client: httpx.AsyncClient, repo_url: str
    ) -> Dict[str, Any]:
        """Per-workflow and overall success percentage across the last 30 runs."""
        try:
            response = await client.get(f"{repo_url}/actions/workflows", headers=self.headers)
            response.raise_for_status()
            workflows = response.json().get("workflows", [])

            workflow_success_pct: Dict[str, float] = {}
            total_successes = 0
            total_runs = 0

            for w in workflows:
                try:
                    runs_resp = await client.get(
                        f"{repo_url}/actions/workflows/{w['id']}/runs",
                        headers=self.headers,
                        params={"exclude_pull_requests": "true"},
                    )
                    runs_resp.raise_for_status()
                    runs = runs_resp.json().get("workflow_runs", [])
                    successes = sum(
                        1 for r in runs
                        if r.get("status") == "completed" and r.get("conclusion") == "success"
                    )
                    if runs:
                        workflow_success_pct[w["name"]] = successes / len(runs) * 100
                        total_successes += successes
                        total_runs += len(runs)
                except Exception as e:
                    logger.error(f"Error fetching runs for workflow {w}: {e}")

            logger.debug(f"Workflow success percentages: {workflow_success_pct}")
            return {
                "workflow_success_percentage": workflow_success_pct,
                "total_workflow_success_percentage": total_successes / max(total_runs, 1) * 100,
            }
        except Exception as e:
            logger.error(f"Error fetching workflows: {e}")
            return {"workflow_success_percentage": {}}

    async def deployment_frequency(
        self,
        client: httpx.AsyncClient,
        repo_url: str,
        days_to_measure: int = 365,
        pages: int = 1,
        page_size: int = 100,
    ) -> Dict[str, Optional[int]]:
        """Number of successful GitHub deployments in the last `days_to_measure` days."""
        key = f"num_of_deployments_last_{days_to_measure}_days"
        try:
            deployments: List[Dict] = []
            for page in range(1, pages + 1):
                resp = await client.get(
                    f"{repo_url}/deployments",
                    headers=self.headers,
                    params={"per_page": page_size, "page": page},
                )
                resp.raise_for_status()
                deployments += resp.json()

            start_date = datetime.now(timezone.utc) - timedelta(days=days_to_measure)
            deployment_count = 0
            for d in deployments:
                if self._parse_github_datetime_string(d["created_at"]) < start_date:
                    break
                try:
                    status_resp = await client.get(d["statuses_url"], headers=self.headers)
                    status_resp.raise_for_status()
                    if any(s["state"] == "success" for s in status_resp.json()):
                        deployment_count += 1
                except Exception as e:
                    logger.error(f"Error fetching deployment statuses: {e}")

            if deployment_count == 0:
                # Distinguish "zero deployments in window" from "repo doesn't use GitHub deployments".
                check = await client.get(f"{repo_url}/deployments", headers=self.headers)
                if not check.json():
                    return {key: None}

            logger.debug(f"Deployments in last {days_to_measure} days: {deployment_count}")
            return {key: deployment_count}
        except Exception as e:
            logger.error(f"Error fetching deployments: {e}")
            return {key: 0}

    async def release_frequency(
        self,
        client: httpx.AsyncClient,
        repo_url: str,
        days_to_measure: int = 365,
        pages: int = 1,
        page_size: int = 100,
    ) -> Dict[str, Optional[int]]:
        """Number of GitHub releases published in the last `days_to_measure` days."""
        key = f"num_of_releases_last_{days_to_measure}_days"
        try:
            releases: List[Dict] = []
            for page in range(1, pages + 1):
                resp = await client.get(
                    f"{repo_url}/releases",
                    headers=self.headers,
                    params={"per_page": page_size, "page": page},
                )
                resp.raise_for_status()
                releases += resp.json()

            start_date = datetime.now(timezone.utc) - timedelta(days=days_to_measure)
            release_count = 0
            for r in releases:
                if not r.get("published_at"):
                    continue
                if self._parse_github_datetime_string(r["published_at"]) >= start_date:
                    release_count += 1
                else:
                    break

            if release_count == 0:
                check = await client.get(f"{repo_url}/releases/latest", headers=self.headers)
                if check.status_code == 404:
                    return {key: None}

            logger.debug(f"Releases in last {days_to_measure} days: {release_count}")
            return {key: release_count}
        except Exception as e:
            logger.error(f"Error fetching releases: {e}")
            return {key: 0}

    async def average_time_failure(
        self, client: httpx.AsyncClient, repo_url: str, branch: str, num_workflows: int = 100
    ) -> Dict[str, float]:
        """Average duration (seconds) of failed workflow runs over the last N runs."""
        runs = await self._get_last_n_workflow_runs(
            client, repo_url, num_workflows, branch, status="failure"
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
        repo_url: str,
        pages: int = 1,
        page_size: int = 100,
    ) -> Dict[str, float]:
        """Average time (seconds) from PR open to merge across the last N closed PRs."""
        try:
            pull_requests: List[Dict] = []
            for page in range(1, pages + 1):
                resp = await client.get(
                    f"{repo_url}/pulls",
                    headers=self.headers,
                    params={"state": "closed", "per_page": page_size, "page": page},
                )
                resp.raise_for_status()
                pull_requests += resp.json()

            total_cycle_time = sum(
                self._parse_github_datetime_string(pr["merged_at"]).timestamp()
                - self._parse_github_datetime_string(pr["created_at"]).timestamp()
                for pr in pull_requests
                if pr.get("merged_at")
            )
            avg = total_cycle_time / max(len(pull_requests), 1)
            logger.debug(f"Average cycle time: {avg:.1f}s")
            return {"average_cycle_time": avg}
        except Exception as e:
            logger.error(f"Error fetching pull requests: {e}")
            return {"average_cycle_time": 0.0}

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    async def _get_last_n_workflow_runs(
        self,
        client: httpx.AsyncClient,
        repo_url: str,
        num_workflows: int = 100,
        branch: str = "main",
        status: Optional[str] = None,
    ) -> List[Dict]:
        """Fetch up to `num_workflows` workflow runs, paging as needed."""
        try:
            results: List[Dict] = []
            remaining = num_workflows
            page = 1
            while remaining > 0:
                params: Dict[str, Any] = {
                    "branch": branch,
                    "per_page": min(remaining, 100),
                    "page": page,
                }
                if status:
                    params["status"] = status
                resp = await client.get(
                    f"{repo_url}/actions/runs", headers=self.headers, params=params
                )
                resp.raise_for_status()
                batch = resp.json().get("workflow_runs", [])
                results += batch
                if len(batch) < params["per_page"]:
                    break
                remaining -= len(batch)
                page += 1
            return results
        except Exception as e:
            logger.error(f"Error fetching workflow runs: {e}")
            return []

    def _parse_repo_url(self, repo_url: str) -> str:
        """Return the GitHub REST API base URL for a repository.

        Examples:
            'https://github.com/owner/repo'     -> 'https://api.github.com/repos/owner/repo'
            'https://github.com/owner/repo.git' -> 'https://api.github.com/repos/owner/repo'
        """
        url = repo_url.rstrip("/").removesuffix(".git")
        if "github.com/" in url:
            parts = url.split("github.com/")[-1].split("/")
            if len(parts) >= 2:
                return f"https://api.github.com/repos/{parts[0]}/{parts[1]}"
        raise ValueError(f"Invalid GitHub URL format: {repo_url}")

    def _parse_github_datetime_string(self, date: str) -> datetime:
        """Parse a GitHub ISO 8601 timestamp into a timezone-aware datetime."""
        return datetime.strptime(date, self.github_datetime_format)
