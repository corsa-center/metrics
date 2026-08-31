"""
Outreach Collector (CASS Report Section 4.2.5)

Measures a project's ability to attract and retain new contributors.

Covers five of the report's eight sub-metrics from data GitHub returns directly:

  - New Contributor Tracking          : authors whose entire history is recent
  - Contributor Retention Analysis    : share of those who came back
  - Contributor Lifecycle Mapping     : one-time / casual / repeat buckets
  - Good First Issue Effectiveness    : newcomer-labelled issue availability
  - Onboarding Infrastructure Assessment : onboarding docs and templates

The remaining three (Contribution Type Diversity, External Event Participation,
Training Material Integration) need data that is not in the repository — event
programmes, course syllabi, non-code contribution records — and stay uncollected.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx

from collectors.rate_limit import search_get
from collectors.sustainability.base import GitHubCollectorBase

logger = logging.getLogger(__name__)

# Labels projects conventionally use to flag newcomer-friendly work.
_NEWCOMER_LABELS = ["good first issue", "help wanted", "good-first-issue", "newcomer"]

# Onboarding resources, grouped so a project gets credit for any variant.
_ONBOARDING_PATHS = {
    "Contributing guide": [
        "CONTRIBUTING.md", "CONTRIBUTING.rst", "CONTRIBUTING",
        ".github/CONTRIBUTING.md", "docs/CONTRIBUTING.md",
    ],
    "Issue templates": [
        ".github/ISSUE_TEMPLATE", ".github/ISSUE_TEMPLATE.md",
    ],
    "Pull request template": [
        ".github/PULL_REQUEST_TEMPLATE.md", ".github/pull_request_template.md",
        "PULL_REQUEST_TEMPLATE.md",
    ],
    "Getting-started guide": [
        "docs/getting-started.md", "docs/getting_started.md", "docs/quickstart.md",
        "doc/getting-started.md", "GETTING_STARTED.md", "docs/source/getting_started.rst",
    ],
}

# Contribution-count boundaries for lifecycle buckets. The report offers
# "one commit, fewer than five commits, or project-specific thresholds";
# fewer-than-five is used for "casual" here.
_CASUAL_MAX_COMMITS = 4

# A contributor counts as retained once they have made more than one contribution.
_RETAINED_MIN_COMMITS = 2

# Window for "new" contributors and recent commit activity.
_RECENT_DAYS = 365

# Pagination caps, mirroring active_maintenance.py's bounded approach.
_MAX_CONTRIBUTOR_PAGES = 5
_MAX_COMMIT_PAGES = 10


class OutreachCollector(GitHubCollectorBase):
    """Collects contributor-growth metrics (Section 4.2.5)."""

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        repo_name = package.get("name", "Unknown")
        owner_repo = self._extract_owner_repo(package.get("repo_url", ""))
        if not owner_repo:
            logger.error(f"Could not extract owner/repo from {package.get('repo_url')}")
            return self._empty_result(repo_name)

        owner, repo = owner_repo
        logger.info(f"Collecting outreach metrics for {repo_name}")

        async with httpx.AsyncClient(timeout=30.0) as client:
            contributors, recent_commits, newcomer_issues, onboarding = await asyncio.gather(
                self._get_contributors(client, owner, repo),
                self._get_recent_commit_authors(client, owner, repo),
                self._get_newcomer_issues(client, owner, repo),
                self._check_onboarding(client, owner, repo),
                return_exceptions=True,
            )

        if isinstance(contributors, Exception):
            logger.warning(f"Contributor fetch failed: {contributors}")
            contributors = []
        if isinstance(recent_commits, Exception):
            logger.warning(f"Recent commit fetch failed: {recent_commits}")
            recent_commits = {}
        if isinstance(newcomer_issues, Exception):
            logger.warning(f"Newcomer issue fetch failed: {newcomer_issues}")
            newcomer_issues = {}
        if isinstance(onboarding, Exception):
            logger.warning(f"Onboarding check failed: {onboarding}")
            onboarding = {"found": [], "missing": [], "details": {}}

        growth = self._analyze_contributor_growth(contributors, recent_commits)

        return {
            "package_name": repo_name,
            "repository": f"{owner}/{repo}",
            "timestamp": self._get_timestamp(),
            "contributor_growth": growth,
            "newcomer_issues": newcomer_issues,
            "onboarding": onboarding,
            "overall_score": self._calculate_score(growth, newcomer_issues, onboarding),
        }

    # ------------------------------------------------------------------ fetch

    async def _get_contributors(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> List[Dict]:
        """All-time contributors with their total contribution counts."""
        contributors: List[Dict] = []
        url = f"https://api.github.com/repos/{owner}/{repo}/contributors?per_page=100"
        for _ in range(_MAX_CONTRIBUTOR_PAGES):
            resp = await client.get(url, headers=self.github_headers)
            if resp.status_code != 200:
                break
            page = resp.json()
            if not isinstance(page, list) or not page:
                break
            contributors.extend(page)
            next_url = self._next_link(resp.headers.get("Link"))
            if not next_url:
                break
            url = next_url
        return contributors

    async def _get_recent_commit_authors(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> Dict[str, int]:
        """Commit counts per author over the recent window."""
        since = (datetime.now(timezone.utc) - timedelta(days=_RECENT_DAYS)).isoformat()
        url = (
            f"https://api.github.com/repos/{owner}/{repo}/commits"
            f"?since={quote(since)}&per_page=100"
        )
        counts: Dict[str, int] = {}
        for _ in range(_MAX_COMMIT_PAGES):
            resp = await client.get(url, headers=self.github_headers)
            if resp.status_code != 200:
                break
            page = resp.json()
            if not isinstance(page, list) or not page:
                break
            for commit in page:
                login = (commit.get("author") or {}).get("login")
                if login:
                    counts[login] = counts.get(login, 0) + 1
            next_url = self._next_link(resp.headers.get("Link"))
            if not next_url:
                break
            url = next_url
        return counts

    async def _get_newcomer_issues(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> Dict[str, Any]:
        """Open and closed counts for each newcomer-friendly label.

        Uses the search API rather than paginating /issues, so each label costs
        two requests and returns an exact total instead of a page count.
        """
        # All labels in one query per state. Comma-separated values in a
        # label: qualifier are ORed, so this is two searches rather than one
        # per label per state — eight became two. Labels containing a space
        # must be quoted or the parser splits them and drops the remainder.
        labels = ",".join(
            f'"{l}"' if " " in l else l for l in _NEWCOMER_LABELS
        )

        counts: Dict[str, int] = {}
        for state in ("open", "closed"):
            q = f'repo:{owner}/{repo} is:issue state:{state} label:{labels}'
            url = f"https://api.github.com/search/issues?q={quote(q)}&per_page=1"
            resp = await search_get(client, url, self.github_headers)
            counts[state] = resp.json().get("total_count", 0) if resp else 0

        return {
            "labels_queried": _NEWCOMER_LABELS,
            "open": counts["open"],
            "closed": counts["closed"],
            "total": counts["open"] + counts["closed"],
        }

    async def _check_onboarding(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> Dict[str, Any]:
        """Which onboarding resources the repository provides."""

        async def check(label: str, paths: List[str]) -> Tuple[str, Optional[str]]:
            for path in paths:
                url = await self._check_file_exists(client, owner, repo, path)
                if url:
                    return label, url
            return label, None

        results = await asyncio.gather(
            *[check(label, paths) for label, paths in _ONBOARDING_PATHS.items()]
        )
        found, missing, details = [], [], {}
        for label, url in results:
            if url:
                found.append(label)
                details[label] = {"exists": True, "url": url}
            else:
                missing.append(label)
                details[label] = {"exists": False}
        return {"found": found, "missing": missing, "details": details}

    @staticmethod
    def _next_link(link_header: Optional[str]) -> Optional[str]:
        """Parse the `next` URL out of a GitHub Link pagination header."""
        if not link_header:
            return None
        for part in link_header.split(","):
            segment = part.strip()
            if 'rel="next"' in segment:
                return segment.split(";")[0].strip().strip("<>")
        return None

    # ---------------------------------------------------------------- analyze

    def _analyze_contributor_growth(
        self, contributors: List[Dict], recent_counts: Dict[str, int]
    ) -> Dict[str, Any]:
        """Derive newcomer, retention and lifecycle figures.

        A contributor is treated as *new* when their all-time contribution count
        is fully accounted for by commits inside the recent window — i.e. they
        have no history before it. This avoids a second pass over the whole
        commit log to find each author's first commit, at the cost of missing
        anyone whose recent commits exceed the pagination cap.
        """
        if not contributors:
            return {
                "total_contributors": 0,
                "new_contributors": 0,
                "retained_new_contributors": 0,
                "retention_rate": None,
                "lifecycle": {"one_time": 0, "casual": 0, "repeat": 0},
            }

        totals = {
            c["login"]: c.get("contributions", 0)
            for c in contributors
            if c.get("login")
        }

        new_contributors = [
            login
            for login, recent in recent_counts.items()
            if login in totals and totals[login] <= recent
        ]
        retained = [
            login for login in new_contributors
            if recent_counts[login] >= _RETAINED_MIN_COMMITS
        ]
        retention_rate = (
            round(len(retained) / len(new_contributors) * 100, 1)
            if new_contributors
            else None
        )

        lifecycle = {"one_time": 0, "casual": 0, "repeat": 0}
        for count in totals.values():
            if count <= 1:
                lifecycle["one_time"] += 1
            elif count <= _CASUAL_MAX_COMMITS:
                lifecycle["casual"] += 1
            else:
                lifecycle["repeat"] += 1

        return {
            "total_contributors": len(totals),
            "new_contributors": len(new_contributors),
            "retained_new_contributors": len(retained),
            "retention_rate": retention_rate,
            "lifecycle": lifecycle,
        }

    def _calculate_score(
        self, growth: Dict, newcomer_issues: Dict, onboarding: Dict
    ) -> Dict[str, Any]:
        """Score the five collected sub-metrics; three remain uncollected."""
        sub: Dict[str, Dict[str, Any]] = {}

        new_count = growth.get("new_contributors", 0)
        sub["new_contributor_tracking"] = {
            "label": "New Contributor Tracking",
            "value": f"{new_count} in last {_RECENT_DAYS // 365} year"
                     + ("s" if _RECENT_DAYS // 365 != 1 else ""),
            "passing": new_count > 0,
        }

        rate = growth.get("retention_rate")
        sub["contributor_retention"] = {
            "label": "Contributor Retention Analysis",
            "value": f"{rate}% of new contributors returned" if rate is not None
                     else "No new contributors to measure",
            # Half of newcomers coming back is a healthy return rate for OSS.
            "passing": rate is not None and rate >= 50,
        }

        lifecycle = growth.get("lifecycle", {})
        repeat = lifecycle.get("repeat", 0)
        total = growth.get("total_contributors", 0)
        sub["contributor_lifecycle"] = {
            "label": "Contributor Lifecycle Mapping",
            "value": f"{lifecycle.get('one_time', 0)} one-time / "
                     f"{lifecycle.get('casual', 0)} casual / {repeat} repeat",
            # A community sustained by more than a handful of regulars.
            "passing": repeat >= 3,
        }

        gfi_total = newcomer_issues.get("total", 0)
        gfi_open = newcomer_issues.get("open", 0)
        sub["good_first_issue"] = {
            "label": "Good First Issue Effectiveness",
            "value": f"{gfi_open} open, {newcomer_issues.get('closed', 0)} closed"
                     if gfi_total else "No newcomer-labelled issues",
            "passing": gfi_open > 0,
        }

        found = onboarding.get("found", [])
        sub["onboarding_infrastructure"] = {
            "label": "Onboarding Infrastructure Assessment",
            "value": f"{len(found)}/{len(_ONBOARDING_PATHS)} resources",
            "detail": ", ".join(found) if found else None,
            # Over half the onboarding resources present.
            "passing": len(found) >= 3,
        }

        for key, label in [
            ("contribution_type_diversity", "Contribution Type Diversity"),
            ("external_event_participation", "External Event Participation"),
            ("training_material_integration", "Training Material Integration"),
        ]:
            sub[key] = {"label": label, "value": None, "passing": False, "not_collected": True}

        score = sum(1 for s in sub.values() if s.get("passing"))
        max_score = len(sub)
        return {
            "score": score,
            "max_score": max_score,
            "percentage": round(score / max_score * 100, 2),
            "sub_scores": sub,
        }

    def _empty_result(self, repo_name: str) -> Dict[str, Any]:
        return {
            "package_name": repo_name,
            "repository": "unknown",
            "timestamp": self._get_timestamp(),
            "contributor_growth": {},
            "newcomer_issues": {},
            "onboarding": {"found": [], "missing": [], "details": {}},
            "overall_score": {"score": 0, "max_score": 8, "percentage": 0, "sub_scores": {}},
        }
