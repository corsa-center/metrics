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
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx

from collectors.rate_limit import search_get
from collectors.ecosystem.base import COLLECTION_GAP, GitHubCollectorBase, RepoTree, RetryingTransport, get_threshold

logger = logging.getLogger(__name__)

# Labels projects conventionally use to flag newcomer-friendly work.
_NEWCOMER_LABELS = ["good first issue", "help wanted", "good-first-issue", "newcomer"]

# Onboarding resources matched against a RepoTree (case-insensitive, whole
# tree) rather than probed one literal path at a time.
_ONBOARDING_LABELS = [
    "Contributing guide", "Issue templates", "Pull request template",
    "Getting-started guide",
]
_CONTRIBUTING_PATHS = [
    "CONTRIBUTING.md", "CONTRIBUTING.rst", "CONTRIBUTING",
    ".github/CONTRIBUTING.md", "docs/CONTRIBUTING.md",
]
_ISSUE_TEMPLATE_DIR = ".github/ISSUE_TEMPLATE"
_ISSUE_TEMPLATE_FILES = [".github/ISSUE_TEMPLATE.md"]
_PR_TEMPLATE_PATHS = [
    ".github/PULL_REQUEST_TEMPLATE.md", ".github/pull_request_template.md",
    "PULL_REQUEST_TEMPLATE.md",
]
# A getting-started guide, under whatever name and location it actually has --
# six enumerated spellings missed 16 of 65 portfolio repos, AMReX's
# Docs/sphinx_documentation/source/GettingStarted.rst among them
# (corsa-center/metrics#49). Matched anywhere under a doc-shaped directory
# rather than at a handful of exact paths.
_GETTING_STARTED_PATTERN = (
    r"(^|/)(docs?|documentation)/.*(getting[-_]?started|quick[-_ ]?start|tutorial)"
)

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

        async with httpx.AsyncClient(timeout=30.0, transport=RetryingTransport()) as client:
            results = await asyncio.gather(
                self._get_contributors(client, owner, repo),
                self._get_recent_commit_authors(client, owner, repo),
                self._get_newcomer_issues(client, owner, repo),
                RepoTree.fetch(client, self.github_headers, owner, repo),
                return_exceptions=True,
            )

        if isinstance(results[0], Exception):
            logger.warning(f"COLLECTION-GAP category=contributors reason=exception:{results[0]!r}")
            contributors, contributors_gap = [], True
        else:
            contributors, contributors_gap = results[0]

        if isinstance(results[1], Exception):
            logger.warning(f"COLLECTION-GAP category=recent_commits reason=exception:{results[1]!r}")
            recent_commits, commits_gap = {}, True
        else:
            recent_commits, commits_gap = results[1]

        if isinstance(results[2], Exception):
            logger.warning(f"COLLECTION-GAP category=newcomer_issues reason=exception:{results[2]!r}")
            newcomer_issues = {"labels_queried": _NEWCOMER_LABELS, "open": 0,
                               "closed": 0, "total": 0, "not_collected": True}
        else:
            newcomer_issues = results[2]

        tree = COLLECTION_GAP if isinstance(results[3], Exception) else results[3]
        if isinstance(results[3], Exception):
            logger.warning(f"COLLECTION-GAP category=onboarding reason=exception:{results[3]!r}")
        onboarding = self._check_onboarding(tree)

        growth = self._analyze_contributor_growth(contributors, recent_commits)

        return {
            "package_name": repo_name,
            "repository": f"{owner}/{repo}",
            "timestamp": self._get_timestamp(),
            "contributor_growth": growth,
            "newcomer_issues": newcomer_issues,
            "onboarding": onboarding,
            "overall_score": self._calculate_score(
                growth, newcomer_issues, onboarding, contributors_gap, commits_gap
            ),
        }

    # ------------------------------------------------------------------ fetch

    async def _get_page(
        self, client: httpx.AsyncClient, url: str, params: Optional[dict] = None
    ) -> tuple:
        """GET a paginated GitHub endpoint.

        Returns (items, next_url) on success (items is [] and next_url is
        None once pagination is confirmed exhausted), or (COLLECTION_GAP,
        None) if this page couldn't actually be fetched.
        """
        try:
            response = await client.get(url, headers=self.github_headers, params=params)
        except Exception as e:
            logger.warning(f"COLLECTION-GAP url={url} status=exception reason={e!r}")
            return COLLECTION_GAP, None
        if response.status_code == 404:
            return [], None
        if response.status_code != 200:
            return COLLECTION_GAP, None
        page = response.json()
        if not isinstance(page, list):
            return [], None
        return page, self._next_link(response.headers.get("Link"))

    async def _get_contributors(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> tuple:
        """All-time contributors with their total contribution counts.

        Returns (contributors, saw_gap). A gap partway through pagination
        still returns whatever pages were fetched before it, but saw_gap
        tells the caller the list may be incomplete.
        """
        contributors: List[Dict] = []
        url = f"https://api.github.com/repos/{owner}/{repo}/contributors"
        params: Optional[dict] = {"per_page": 100}
        saw_gap = False
        for _ in range(_MAX_CONTRIBUTOR_PAGES):
            page, next_url = await self._get_page(client, url, params)
            if page is COLLECTION_GAP:
                saw_gap = True
                break
            if not page:
                break
            contributors.extend(page)
            if not next_url:
                break
            url, params = next_url, None
        return contributors, saw_gap

    async def _get_recent_commit_authors(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> tuple:
        """Commit counts per author over the recent window.

        Returns (counts, saw_gap).
        """
        since = (datetime.now(timezone.utc) - timedelta(days=_RECENT_DAYS)).isoformat()
        url = f"https://api.github.com/repos/{owner}/{repo}/commits"
        params: Optional[dict] = {"since": since, "per_page": 100}
        counts: Dict[str, int] = {}
        saw_gap = False
        for _ in range(_MAX_COMMIT_PAGES):
            page, next_url = await self._get_page(client, url, params)
            if page is COLLECTION_GAP:
                saw_gap = True
                break
            if not page:
                break
            for commit in page:
                login = (commit.get("author") or {}).get("login")
                if login:
                    counts[login] = counts.get(login, 0) + 1
            if not next_url:
                break
            url, params = next_url, None
        return counts, saw_gap

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

        async def count(state: str) -> tuple:
            q = f'repo:{owner}/{repo} is:issue state:{state} label:{labels}'
            url = f"https://api.github.com/search/issues?q={quote(q)}&per_page=1"
            resp = await search_get(client, url, self.github_headers)
            if resp is None:
                return 0, True
            return resp.json().get("total_count", 0), False

        (open_count, open_gap), (closed_count, closed_gap) = await asyncio.gather(
            count("open"), count("closed"),
        )
        result = {
            "labels_queried": _NEWCOMER_LABELS,
            "open": open_count,
            "closed": closed_count,
            "total": open_count + closed_count,
        }
        # Only the open count drives good_first_issue's passing check below;
        # a gap on the closed-state search alone doesn't make an already
        # confirmed nonzero open count untrustworthy.
        if open_gap and open_count == 0:
            result["not_collected"] = True
        return result

    def _check_onboarding(self, tree) -> Dict[str, Any]:
        """Which onboarding resources the repository provides, resolved
        against a RepoTree instead of probed one literal path at a time --
        see corsa-center/metrics#49 and METRIC_BLIND_SPOTS.md class F2.
        """
        if tree is COLLECTION_GAP:
            return {
                "found": [], "missing": [], "not_collected": list(_ONBOARDING_LABELS),
                "details": {label: {"not_collected": True} for label in _ONBOARDING_LABELS},
            }

        issue_templates_url = tree.match_url(_ISSUE_TEMPLATE_FILES)
        if not issue_templates_url and tree.has_dir(_ISSUE_TEMPLATE_DIR):
            issue_templates_url = f"https://github.com/{tree.owner}/{tree.repo}/tree/HEAD/{_ISSUE_TEMPLATE_DIR}"

        urls = {
            "Contributing guide": tree.match_url(_CONTRIBUTING_PATHS),
            "Issue templates": issue_templates_url,
            "Pull request template": tree.match_url(_PR_TEMPLATE_PATHS),
            "Getting-started guide": tree.find_url(_GETTING_STARTED_PATTERN),
        }
        found = [label for label in _ONBOARDING_LABELS if urls[label]]
        missing = [label for label in _ONBOARDING_LABELS if not urls[label]]
        details = {
            label: ({"exists": True, "url": urls[label]} if urls[label] else {"exists": False})
            for label in _ONBOARDING_LABELS
        }
        return {"found": found, "missing": missing, "not_collected": [], "details": details}

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
        retained_min_commits = get_threshold("4.2.5", "Contributor Retention Analysis", "retained_min_commits")
        retained = [
            login for login in new_contributors
            if recent_counts[login] >= retained_min_commits
        ]
        retention_rate = (
            round(len(retained) / len(new_contributors) * 100, 1)
            if new_contributors
            else None
        )

        casual_max_commits = get_threshold("4.2.5", "Contributor Lifecycle Mapping", "casual_max_commits")
        lifecycle = {"one_time": 0, "casual": 0, "repeat": 0}
        for count in totals.values():
            if count <= 1:
                lifecycle["one_time"] += 1
            elif count <= casual_max_commits:
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
        self, growth: Dict, newcomer_issues: Dict, onboarding: Dict,
        contributors_gap: bool = False, commits_gap: bool = False,
    ) -> Dict[str, Any]:
        """Score the five collected sub-metrics; three remain uncollected."""
        sub: Dict[str, Dict[str, Any]] = {}

        new_count = growth.get("new_contributors", 0)
        new_passing = new_count > 0
        new_entry: Dict[str, Any] = {
            "label": "New Contributor Tracking",
            "value": f"{new_count} in last {_RECENT_DAYS // 365} year"
                     + ("s" if _RECENT_DAYS // 365 != 1 else ""),
            "passing": new_passing,
        }
        if not new_passing and (contributors_gap or commits_gap):
            new_entry["not_collected"] = True
        sub["new_contributor_tracking"] = new_entry

        rate = growth.get("retention_rate")
        retention_passing = rate is not None and rate >= get_threshold("4.2.5", "Contributor Retention Analysis", "min_retention_rate_pct")
        retention_entry: Dict[str, Any] = {
            "label": "Contributor Retention Analysis",
            "value": f"{rate}% of new contributors returned" if rate is not None
                     else "No new contributors to measure",
            "passing": retention_passing,
        }
        if not retention_passing and (contributors_gap or commits_gap):
            retention_entry["not_collected"] = True
        sub["contributor_retention"] = retention_entry

        lifecycle = growth.get("lifecycle", {})
        repeat = lifecycle.get("repeat", 0)
        lifecycle_passing = repeat >= get_threshold("4.2.5", "Contributor Lifecycle Mapping", "min_repeat_contributors")
        lifecycle_entry: Dict[str, Any] = {
            "label": "Contributor Lifecycle Mapping",
            "value": f"{lifecycle.get('one_time', 0)} one-time / "
                     f"{lifecycle.get('casual', 0)} casual / {repeat} repeat",
            "passing": lifecycle_passing,
        }
        if not lifecycle_passing and contributors_gap:
            lifecycle_entry["not_collected"] = True
        sub["contributor_lifecycle"] = lifecycle_entry

        gfi_total = newcomer_issues.get("total", 0)
        gfi_open = newcomer_issues.get("open", 0)
        gfi_passing = gfi_open > 0
        gfi_entry: Dict[str, Any] = {
            "label": "Good First Issue Effectiveness",
            "value": f"{gfi_open} open, {newcomer_issues.get('closed', 0)} closed"
                     if gfi_total else "No newcomer-labelled issues",
            "passing": gfi_passing,
        }
        if not gfi_passing and newcomer_issues.get("not_collected"):
            gfi_entry["not_collected"] = True
        sub["good_first_issue"] = gfi_entry

        found = onboarding.get("found", [])
        onboarding_passing = len(found) >= get_threshold("4.2.5", "Onboarding Infrastructure Assessment")
        onboarding_entry: Dict[str, Any] = {
            "label": "Onboarding Infrastructure Assessment",
            "value": f"{len(found)}/{len(_ONBOARDING_LABELS)} resources",
            "detail": ", ".join(found) if found else None,
            "passing": onboarding_passing,
        }
        if not onboarding_passing and onboarding.get("not_collected"):
            onboarding_entry["not_collected"] = True
        sub["onboarding_infrastructure"] = onboarding_entry

        for key, label in [
            ("contribution_type_diversity", "Contribution Type Diversity"),
            ("external_event_participation", "External Event Participation"),
            ("training_material_integration", "Training Material Integration"),
        ]:
            sub[key] = {"label": label, "value": None, "passing": False, "not_collected": True}

        scorable = {k: v for k, v in sub.items() if not v.get("not_collected")}
        score = sum(1 for s in scorable.values() if s.get("passing"))
        max_score = len(scorable)
        if not max_score:
            return {
                "score": None, "max_score": 0, "percentage": None,
                "status": "not_collected", "sub_scores": sub,
            }
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
            "onboarding": {"found": [], "missing": [], "not_collected": [], "details": {}},
            "overall_score": {"score": 0, "max_score": 5, "percentage": 0, "sub_scores": {}},
        }
