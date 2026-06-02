"""
Engagement Collector (CASS Report Section 4.2.4)

Measures how responsive and interactive a project is with its community by
computing statistics from the GitHub issues and pull requests APIs:

  - Median time to first non-bot response on issues
  - Median issue close time (open → closed)
  - Open-to-closed issue ratio (backlog signal)
  - PR merge rate (accepted vs closed-without-merge)
  - Median PR cycle time (open → merged)

A sample of the most-recent 30 issues and 30 PRs is used to keep API
calls manageable while still reflecting current project behaviour.
"""

import asyncio
import httpx
import logging
import statistics
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from collectors.sustainability.base import GitHubCollectorBase

logger = logging.getLogger(__name__)

_SAMPLE = 30
_MAINTAINER_ROLES = {"COLLABORATOR", "MEMBER", "OWNER"}
_API = "https://api.github.com/repos/{owner}/{repo}"


def _is_bot(login: str) -> bool:
    return login.endswith("[bot]") or login.endswith("-bot")


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _hours(a: Optional[datetime], b: Optional[datetime]) -> Optional[float]:
    if a and b:
        return abs((b - a).total_seconds()) / 3600
    return None


class EngagementCollector(GitHubCollectorBase):
    """Collects engagement metrics from GitHub issues and PRs (§4.2.4)."""

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        repo_name = package.get("name", "Unknown")
        repo_url = package.get("repo_url", "")

        owner_repo = self._extract_owner_repo(repo_url)
        if not owner_repo:
            logger.error(f"Could not extract owner/repo from {repo_url}")
            return self._empty_result(repo_name)

        owner, repo = owner_repo
        logger.info(f"Collecting engagement metrics for {owner}/{repo}")

        base = _API.format(owner=owner, repo=repo)

        async with httpx.AsyncClient(timeout=30.0) as client:
            issues_raw, prs_raw, repo_info = await asyncio.gather(
                self._fetch_issues(client, base),
                self._fetch_prs(client, base),
                self._fetch_repo_info(client, base),
            )

            # Fetch first comments for each issue concurrently (bot-filtered).
            first_responses = await asyncio.gather(
                *[self._first_response_hours(client, base, i) for i in issues_raw]
            )

        issue_stats = self._compute_issue_stats(issues_raw, list(first_responses))
        pr_stats = self._compute_pr_stats(prs_raw)
        backlog = self._compute_backlog(repo_info, issues_raw)
        score = self._score(issue_stats, pr_stats, backlog)

        return {
            "package_name": repo_name,
            "repository": f"{owner}/{repo}",
            "timestamp": self._get_timestamp(),
            "issue_stats": issue_stats,
            "pr_stats": pr_stats,
            "backlog": backlog,
            "overall_score": score,
        }

    # ------------------------------------------------------------------ #
    # Fetchers                                                             #
    # ------------------------------------------------------------------ #

    async def _fetch_issues(
        self, client: httpx.AsyncClient, base: str
    ) -> List[Dict]:
        try:
            resp = await client.get(
                f"{base}/issues",
                headers=self.github_headers,
                params={"state": "all", "per_page": _SAMPLE, "sort": "updated", "direction": "desc"},
            )
            resp.raise_for_status()
            # Exclude pull requests (GitHub issues API returns both)
            return [i for i in resp.json() if "pull_request" not in i]
        except Exception as e:
            logger.warning(f"Issues fetch failed: {e}")
            return []

    async def _fetch_prs(
        self, client: httpx.AsyncClient, base: str
    ) -> List[Dict]:
        try:
            resp = await client.get(
                f"{base}/pulls",
                headers=self.github_headers,
                params={"state": "closed", "per_page": _SAMPLE, "sort": "updated", "direction": "desc"},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning(f"PRs fetch failed: {e}")
            return []

    async def _fetch_repo_info(
        self, client: httpx.AsyncClient, base: str
    ) -> Dict:
        try:
            resp = await client.get(base, headers=self.github_headers)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning(f"Repo info fetch failed: {e}")
            return {}

    async def _first_response_hours(
        self, client: httpx.AsyncClient, base: str, issue: Dict
    ) -> Optional[float]:
        """Return hours from issue creation to first non-bot comment, or None."""
        if issue.get("comments", 0) == 0:
            return None
        number = issue["number"]
        created = _parse_dt(issue.get("created_at"))
        if not created:
            return None
        try:
            resp = await client.get(
                f"{base}/issues/{number}/comments",
                headers=self.github_headers,
                params={"per_page": 10},
            )
            resp.raise_for_status()
            for comment in resp.json():
                login = comment.get("user", {}).get("login", "")
                if _is_bot(login):
                    continue
                first_comment_dt = _parse_dt(comment.get("created_at"))
                return _hours(created, first_comment_dt)
        except Exception as e:
            logger.debug(f"Comment fetch failed for issue {number}: {e}")
        return None

    # ------------------------------------------------------------------ #
    # Statistics                                                           #
    # ------------------------------------------------------------------ #

    def _compute_issue_stats(
        self, issues: List[Dict], response_times: List[Optional[float]]
    ) -> Dict[str, Any]:
        close_times = []
        maintainer_first = 0
        total_with_response = 0

        for issue in issues:
            created = _parse_dt(issue.get("created_at"))
            closed = _parse_dt(issue.get("closed_at"))
            h = _hours(created, closed)
            if h is not None:
                close_times.append(h)

        valid_responses = [t for t in response_times if t is not None]

        return {
            "sample_size": len(issues),
            "median_first_response_hours": round(statistics.median(valid_responses), 1) if valid_responses else None,
            "median_close_time_hours": round(statistics.median(close_times), 1) if close_times else None,
            "pct_with_response": round(len(valid_responses) / len(issues) * 100, 1) if issues else 0.0,
        }

    def _compute_pr_stats(self, prs: List[Dict]) -> Dict[str, Any]:
        merged, closed_no_merge, cycle_times = 0, 0, []

        for pr in prs:
            if pr.get("merged_at"):
                merged += 1
                created = _parse_dt(pr.get("created_at"))
                merged_at = _parse_dt(pr.get("merged_at"))
                h = _hours(created, merged_at)
                if h is not None:
                    cycle_times.append(h)
            else:
                closed_no_merge += 1

        total = merged + closed_no_merge
        return {
            "sample_size": total,
            "merged": merged,
            "closed_without_merge": closed_no_merge,
            "merge_rate_pct": round(merged / total * 100, 1) if total else None,
            "median_cycle_time_hours": round(statistics.median(cycle_times), 1) if cycle_times else None,
        }

    def _compute_backlog(self, repo_info: Dict, issues: List[Dict]) -> Dict[str, Any]:
        open_count = repo_info.get("open_issues_count")  # includes open PRs
        closed_in_sample = sum(1 for i in issues if i.get("state") == "closed")
        open_in_sample = sum(1 for i in issues if i.get("state") == "open")
        sample_ratio = (
            round(open_in_sample / closed_in_sample, 2) if closed_in_sample else None
        )
        return {
            "repo_open_issues": open_count,
            "sample_open": open_in_sample,
            "sample_closed": closed_in_sample,
            "sample_open_to_closed_ratio": sample_ratio,
        }

    # ------------------------------------------------------------------ #
    # Scoring (0–100)                                                      #
    # ------------------------------------------------------------------ #

    def _score(
        self,
        issue_stats: Dict[str, Any],
        pr_stats: Dict[str, Any],
        backlog: Dict[str, Any],
    ) -> Dict[str, Any]:
        pts = 0

        # First response time (25 pts)
        frt = issue_stats.get("median_first_response_hours")
        if frt is not None:
            if frt < 24:
                pts += 25
            elif frt < 168:
                pts += 15
            elif frt < 720:
                pts += 5

        # Median close time (25 pts)
        mct = issue_stats.get("median_close_time_hours")
        if mct is not None:
            if mct < 168:
                pts += 25
            elif mct < 720:
                pts += 15
            elif mct < 2160:
                pts += 5

        # PR merge rate (25 pts)
        mrp = pr_stats.get("merge_rate_pct")
        if mrp is not None:
            if mrp > 75:
                pts += 25
            elif mrp > 50:
                pts += 15
            elif mrp > 25:
                pts += 5

        # Open/closed ratio (25 pts) — lower is better
        ratio = backlog.get("sample_open_to_closed_ratio")
        if ratio is not None:
            if ratio < 0.5:
                pts += 25
            elif ratio < 1.0:
                pts += 15
            elif ratio < 2.0:
                pts += 5

        return {
            "score": pts,
            "max_score": 100,
            "percentage": float(pts),
        }

    def _empty_result(self, repo_name: str) -> Dict[str, Any]:
        return {
            "package_name": repo_name,
            "repository": "unknown",
            "timestamp": self._get_timestamp(),
            "issue_stats": {},
            "pr_stats": {},
            "backlog": {},
            "overall_score": {"score": 0, "max_score": 100, "percentage": 0.0},
        }
