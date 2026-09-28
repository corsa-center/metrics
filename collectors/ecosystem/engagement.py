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

from collectors.ecosystem.base import GitHubCollectorBase, RetryingTransport, get_threshold

logger = logging.getLogger(__name__)

_SAMPLE = 30
# Pages of 100 to pull while filtering out pull requests. Four is enough to
# reach 30 issues even when ~87% of recent activity is PRs, as on HDF5.
_MAX_ISSUE_PAGES = 4
# Below this many discussion-shaped issues (see _is_internal_triage), a
# median comment count or an answered-within-a-week share is one or two
# issues' worth of noise, so those rows are reported but not scored.
_MIN_DISCUSSION_SAMPLE = 5
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


def _is_internal_triage(issue: Dict) -> bool:
    """Maintainer-filed, zero-comment issues are self-contained triage
    records -- a defect ticket immediately closed by the PR that fixes it --
    not a conversation anyone was waiting on an answer to."""
    return issue.get("author_association") in _INSIDE_ASSOCIATIONS and issue.get("comments", 0) == 0


def _hours(a: Optional[datetime], b: Optional[datetime]) -> Optional[float]:
    if a and b:
        return abs((b - a).total_seconds()) / 3600
    return None


# Response consistency is measured as the share of issues answered inside a
# window rather than as a p90/median ratio. The ratio is scale-sensitive: a
# project that usually replies within minutes scores thousands-to-one the
# moment a single issue waits a fortnight, which says more about the
# arithmetic than about the project. The absolute question — does everyone
# get an answer, or only some people — is what the report is actually asking.

# Share of issues and PRs opened by people outside the maintainer group.
# GitHub's author_association marks OWNER / MEMBER / COLLABORATOR as inside.
_INSIDE_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}


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

        async with httpx.AsyncClient(timeout=30.0, transport=RetryingTransport()) as client:
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
        """Fetch a real sample of issues, paging past pull requests.

        The issues endpoint returns PRs as well and offers no way to exclude
        them, so a single page of _SAMPLE items yields almost no issues on a
        PR-heavy repository — HDF5's most recent 30 entries are 26 PRs and 4
        issues, which is far too small a sample for a median to mean anything.
        Pages of 100 are pulled until _SAMPLE discussion-shaped issues are in
        hand: on a repository whose maintainers file their own triage tickets
        (AMReX: 25 of its newest 30 issues), a flat 30-issue cut leaves the
        discussion metrics judging a handful of real conversations.
        """
        issues: List[Dict] = []
        discussable = 0
        try:
            for page in range(1, _MAX_ISSUE_PAGES + 1):
                resp = await client.get(
                    f"{base}/issues",
                    headers=self.github_headers,
                    params={
                        "state": "all", "per_page": 100, "page": page,
                        "sort": "updated", "direction": "desc",
                    },
                )
                resp.raise_for_status()
                batch = resp.json()
                if not batch:
                    break
                # Exclude pull requests (GitHub issues API returns both)
                for item in batch:
                    if "pull_request" in item:
                        continue
                    issues.append(item)
                    discussable += not _is_internal_triage(item)
                    if discussable >= _SAMPLE:
                        return issues
            return issues
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

        # Internal triage tickets (_is_internal_triage) counted the same as
        # an unanswered community question misread a deliberate, effective
        # triage workflow as disengagement. Excluded only from the
        # discussion-shaped metrics below (comment depth, timely-answer
        # share, outside-participation share); close time and first-response
        # time aren't affected -- a fast, silent close doesn't misrepresent
        # those the same way.
        discussable_idx = [n for n, i in enumerate(issues) if not _is_internal_triage(i)]
        discussable = [issues[n] for n in discussable_idx]

        # Interaction depth and how evenly responses are distributed.
        comment_counts = [i.get("comments", 0) for i in discussable]
        median_comments = (
            round(statistics.median(comment_counts), 1) if comment_counts else None
        )
        # Share of the discussion-shaped sample answered inside the window.
        # A community issue with no response at all counts against it -- the
        # clearest case of inconsistent engagement. Computed over the same
        # issues as comment depth: dividing by the whole sample scored AMReX
        # at 10% although every outside issue had been answered.
        timely_share = None
        if discussable:
            response_window_hours = get_threshold("4.2.4", "Communication Pattern Analysis", "response_window_hours")
            timely = sum(
                1 for n in discussable_idx
                if n < len(response_times) and response_times[n] is not None
                and response_times[n] <= response_window_hours
            )
            timely_share = round(timely / len(discussable), 3)

        outside = sum(
            1 for i in discussable
            if i.get("author_association") not in _INSIDE_ASSOCIATIONS
        )

        return {
            "sample_size": len(issues),
            # Denominator for Community Participation Assessment -- see
            # `discussable` above.
            "discussion_sample_size": len(discussable),
            "internal_triage_excluded": len(issues) - len(discussable),
            "median_first_response_hours": round(statistics.median(valid_responses), 1) if valid_responses else None,
            "median_close_time_hours": round(statistics.median(close_times), 1) if close_times else None,
            "pct_with_response": round(len(valid_responses) / len(issues) * 100, 1) if issues else 0.0,
            "median_comments": median_comments,
            "timely_response_share": timely_share,
            "outside_authors": outside,
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

        outside = sum(
            1 for pr in prs
            if pr.get("author_association") not in _INSIDE_ASSOCIATIONS
        )

        total = merged + closed_no_merge
        return {
            "sample_size": total,
            "merged": merged,
            "closed_without_merge": closed_no_merge,
            "outside_authors": outside,
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
    # Scoring (0–7, one point per PDF sub-metric)                         #
    # ------------------------------------------------------------------ #

    def _score(
        self,
        issue_stats: Dict[str, Any],
        pr_stats: Dict[str, Any],
        backlog: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Score each of the 7 measurement methods defined in CASS Report §4.2.4.
        Each sub-metric contributes 1 point if collected and passing its threshold.
        Sub-metrics not yet implemented contribute 0 and are flagged not_collected.
        """
        sub = {}
        pts = 0

        # 1. Response Time Tracking — passing if median first response is under the cap
        frt = issue_stats.get("median_first_response_hours")
        passing = frt is not None and frt < get_threshold("4.2.4", "Response Time Tracking")
        sub["response_time_tracking"] = {
            "label": "Response Time Tracking",
            "value": f"{frt:.0f} hours" if frt is not None else None,
            "passing": passing,
            "pts": 1 if passing else 0,
        }
        pts += sub["response_time_tracking"]["pts"]

        # 2. Issue Resolution Analysis — passing if median close time is under the cap
        mct = issue_stats.get("median_close_time_hours")
        passing = mct is not None and mct < get_threshold("4.2.4", "Issue Resolution Analysis")
        sub["issue_resolution"] = {
            "label": "Issue Resolution Analysis",
            "value": f"{mct:.0f} hours" if mct is not None else None,
            "passing": passing,
            "pts": 1 if passing else 0,
        }
        pts += sub["issue_resolution"]["pts"]

        # 3. Pull Request Flow Assessment — passing if merge rate is above the floor
        mrp = pr_stats.get("merge_rate_pct")
        passing = mrp is not None and mrp > get_threshold("4.2.4", "Pull Request Flow Assessment")
        sub["pr_flow"] = {
            "label": "Pull Request Flow Assessment",
            "value": f"{mrp:.0f}%" if mrp is not None else None,
            "passing": passing,
            "pts": 1 if passing else 0,
        }
        pts += sub["pr_flow"]["pts"]

        # 4. Support Request Closure Analysis — passing if open/closed ratio is under the cap
        ratio = backlog.get("sample_open_to_closed_ratio")
        passing = ratio is not None and ratio < get_threshold("4.2.4", "Support Request Closure Analysis")
        sub["support_closure"] = {
            "label": "Support Request Closure Analysis",
            "value": f"{ratio:.2f}" if ratio is not None else None,
            "passing": passing,
            "pts": 1 if passing else 0,
        }
        pts += sub["support_closure"]["pts"]

        discussion_n = issue_stats.get("discussion_sample_size", 0)
        thin_sample = 0 < discussion_n < _MIN_DISCUSSION_SAMPLE
        unscored = 0

        # 5. Engagement Quality Metrics — depth of discussion per issue
        mc = issue_stats.get("median_comments")
        passing = mc is not None and mc >= get_threshold("4.2.4", "Engagement Quality Metrics")
        sub["engagement_quality"] = {
            "label": "Engagement Quality Metrics",
            "value": f"{mc:g} comments per issue (median)" if mc is not None else None,
            "passing": passing,
            "pts": 1 if passing else 0,
        }
        if thin_sample:
            self._mark_thin_sample(sub["engagement_quality"], discussion_n)
            unscored += 1
        pts += sub["engagement_quality"]["pts"]

        # 6. Communication Pattern Analysis — whether everyone gets an answer,
        # not just the typical reporter.
        timely = issue_stats.get("timely_response_share")
        passing = timely is not None and timely >= get_threshold("4.2.4", "Communication Pattern Analysis", "min_timely_response_share")
        sub["communication_patterns"] = {
            "label": "Communication Pattern Analysis",
            "value": f"{timely * 100:.0f}% of community issues answered within a week"
                     if timely is not None else "No issues to assess",
            "passing": passing,
            "pts": 1 if passing else 0,
        }
        if thin_sample:
            self._mark_thin_sample(sub["communication_patterns"], discussion_n)
            unscored += 1
        pts += sub["communication_patterns"]["pts"]

        # 7. Community Participation Assessment — work arriving from outside the
        # maintainer group, across both issues and pull requests. Uses the
        # discussion-shaped issue count (excludes maintainer-filed,
        # zero-comment triage tickets), same reasoning as Engagement Quality
        # above -- see _compute_issue_stats.
        issue_n = issue_stats.get("discussion_sample_size", 0)
        pr_n = pr_stats.get("sample_size", 0)
        total_n = issue_n + pr_n
        outside_n = (
            issue_stats.get("outside_authors", 0) + pr_stats.get("outside_authors", 0)
        )
        share = outside_n / total_n if total_n else None
        passing = share is not None and share >= get_threshold("4.2.4", "Community Participation Assessment")
        sub["community_participation"] = {
            "label": "Community Participation Assessment",
            "value": f"{share * 100:.0f}% of {total_n} issues and PRs opened from "
                     f"outside the maintainer group" if share is not None else None,
            "passing": passing,
            "pts": 1 if passing else 0,
        }
        pts += sub["community_participation"]["pts"]

        return {
            "score": pts,
            "max_score": 7 - unscored,
            "sub_scores": sub,
        }

    @staticmethod
    def _mark_thin_sample(entry: Dict[str, Any], n: int) -> None:
        """Keep the measured value visible but take the row out of the score."""
        entry["value"] = (f"{entry['value']} -- only {n} community issue(s) sampled, "
                          f"too few to judge")
        entry["insufficient_sample"] = True
        entry["passing"] = False
        entry["pts"] = 0

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
