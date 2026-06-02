"""
CHAOSS Activity Metrics Collector (CASS Report Section 4.2.4)

Collects CHAOSS-defined metrics covering:
- Issues Inclusivity: Unique issue author count as a proxy for participation breadth
  (NOTE: this is a rough proxy — it counts authors, not demographic diversity)
- Documentation Usability: README quality and documentation presence
- Time to Close: Average time to resolve issues
- Change Request Closure Ratio: Percentage of PRs merged vs closed without merge
- Project Popularity: Stars, forks, watchers
- Issue Age: Distribution of open issue ages
- Release Frequency: Cadence of new releases

CHAOSS Metrics Reference: https://chaoss.community/metrics/
"""

import asyncio
import base64
import httpx
import logging
import re
from datetime import datetime, timezone, timedelta
from statistics import mean, median
from typing import Any, Dict, List, Optional

from collectors.sustainability.base import GitHubCollectorBase

logger = logging.getLogger(__name__)

# Scoring thresholds — chosen to reflect typical OSS project health ranges.
# Stars: 1000 stars ≈ 100 points; Forks: 200 forks ≈ 100; Watchers: 100 ≈ 100.
_POPULARITY_STAR_SCALE = 1000
_POPULARITY_FORK_SCALE = 200
_POPULARITY_WATCH_SCALE = 100

# Time-to-close brackets (days → score): faster response earns higher score.
_TIME_CLOSE_BRACKETS = [(7, 100), (30, 80), (90, 60), (180, 40)]
_TIME_CLOSE_DEFAULT_SCORE = 20

# Release frequency brackets (avg days between releases → score).
_RELEASE_FREQ_BRACKETS = [(30, 100), (90, 80), (180, 60), (365, 40)]
_RELEASE_FREQ_DEFAULT_SCORE = 20

# Issue age brackets (avg open issue age in days → score).
_ISSUE_AGE_BRACKETS = [(30, 100), (90, 80), (180, 60), (365, 40)]
_ISSUE_AGE_DEFAULT_SCORE = 20

# Issues are considered stale after this many days open.
_STALE_ISSUE_DAYS = 180


def _bracket_score(value: float, brackets: list, default: int) -> int:
    """Return a score by finding the first bracket threshold >= value."""
    for threshold, score in brackets:
        if value <= threshold:
            return score
    return default


class CHAOSSGovernanceCollector(GitHubCollectorBase):
    """Collects CHAOSS-defined activity health indicators (Section 4.2.4)."""

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        repo_name = package.get("name", "Unknown")
        repo_url = package.get("repo_url", "")

        logger.info(f"Collecting CHAOSS metrics for {repo_name}")

        owner_repo = self._extract_owner_repo(repo_url)
        if not owner_repo:
            logger.error(f"Could not extract owner/repo from {repo_url}")
            return self._empty_result(repo_name)

        owner, repo = owner_repo

        async with httpx.AsyncClient(timeout=30.0) as client:
            results = await asyncio.gather(
                self._get_project_popularity(client, owner, repo),
                self._get_documentation_usability(client, owner, repo),
                self._get_issue_metrics(client, owner, repo),
                self._get_change_request_metrics(client, owner, repo),
                self._get_release_frequency(client, owner, repo),
                self._get_issues_inclusivity(client, owner, repo),
                return_exceptions=True,
            )

        popularity = results[0] if not isinstance(results[0], Exception) else {}
        documentation = results[1] if not isinstance(results[1], Exception) else {}
        issue_metrics = results[2] if not isinstance(results[2], Exception) else {}
        pr_metrics = results[3] if not isinstance(results[3], Exception) else {}
        release_freq = results[4] if not isinstance(results[4], Exception) else {}
        inclusivity = results[5] if not isinstance(results[5], Exception) else {}

        overall_score = self._calculate_overall_score(
            popularity, documentation, issue_metrics, pr_metrics, release_freq, inclusivity
        )

        return {
            "package_name": repo_name,
            "repository": f"{owner}/{repo}",
            "timestamp": self._get_timestamp(),
            "chaoss_metrics": {
                "project_popularity": popularity,
                "documentation_usability": documentation,
                "time_to_close": issue_metrics.get("time_to_close", {}),
                "issue_age": issue_metrics.get("issue_age", {}),
                "change_request_closure_ratio": pr_metrics.get("closure_ratio", {}),
                "release_frequency": release_freq,
                "issues_inclusivity": inclusivity,
            },
            "overall_score": overall_score,
            "assessment_method": "chaoss_activity_metrics",
            "chaoss_reference": "https://chaoss.community/metrics/",
        }

    # ------------------------------------------------------------------ #
    # Metric collectors                                                    #
    # ------------------------------------------------------------------ #

    async def _get_project_popularity(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> Dict[str, Any]:
        """CHAOSS: Project Popularity — stars, forks, watchers."""
        url = f"https://api.github.com/repos/{owner}/{repo}"
        try:
            response = await client.get(url, headers=self.github_headers)
            if response.status_code != 200:
                logger.warning(f"Popularity fetch failed: {response.status_code}")
                return {}
            data = response.json()
            stars = data.get("stargazers_count", 0)
            forks = data.get("forks_count", 0)
            watchers = data.get("watchers_count", 0)
            star_score = min(100, (stars / _POPULARITY_STAR_SCALE) * 100) if stars else 0
            fork_score = min(100, (forks / _POPULARITY_FORK_SCALE) * 100) if forks else 0
            watch_score = min(100, (watchers / _POPULARITY_WATCH_SCALE) * 100) if watchers else 0
            avg_score = (star_score + fork_score + watch_score) / 3
            logger.info(f"  Stars: {stars}, Forks: {forks}, Watchers: {watchers}")
            return {
                "stars": stars,
                "forks": forks,
                "watchers": watchers,
                "subscribers": data.get("subscribers_count", 0),
                "score": round(avg_score, 2),
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
            }
        except Exception as e:
            logger.error(f"Error getting popularity: {e}")
            return {}

    async def _get_documentation_usability(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> Dict[str, Any]:
        """CHAOSS: Documentation Usability — README quality and docs presence."""
        found_docs = []
        doc_details: Dict[str, Any] = {}

        readme_data = await self._get_readme_content(client, owner, repo)
        if readme_data:
            found_docs.append("readme")
            quality = self._assess_readme_quality(readme_data.get("content", ""))
            doc_details["readme"] = {"exists": True, "size": readme_data.get("size", 0), "quality_score": quality}
            logger.info(f"  README found (quality: {quality}/100)")
        else:
            doc_details["readme"] = {"exists": False, "quality_score": 0}

        for pattern in ["CONTRIBUTING.md", ".github/CONTRIBUTING.md"]:
            if await self._check_file_exists(client, owner, repo, pattern):
                found_docs.append("contributing")
                doc_details["contributing"] = {"exists": True, "file": pattern}
                break
        else:
            doc_details["contributing"] = {"exists": False}

        for pattern in ["docs/", "documentation/", "doc/"]:
            if await self._check_file_exists(client, owner, repo, pattern):
                found_docs.append("docs_folder")
                doc_details["docs_folder"] = {"exists": True, "path": pattern}
                break
        else:
            doc_details["docs_folder"] = {"exists": False}

        has_wiki = await self._check_wiki_enabled(client, owner, repo)
        if has_wiki:
            found_docs.append("wiki")
        doc_details["wiki"] = {"exists": has_wiki}

        max_docs = 4
        base_score = (len(found_docs) / max_docs) * 100
        readme_quality = doc_details.get("readme", {}).get("quality_score", 0)
        doc_score = (base_score * 0.6) + (readme_quality * 0.4)

        return {
            "score": round(doc_score, 2),
            "found": found_docs,
            "details": doc_details,
            "count": len(found_docs),
            "max_count": max_docs,
        }

    async def _get_issue_metrics(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> Dict[str, Any]:
        """CHAOSS: Time to Close and Issue Age."""
        closed_issues = await self._get_closed_issues(client, owner, repo, limit=30)
        open_issues = await self._get_open_issues(client, owner, repo, limit=50)
        return {
            "time_to_close": self._calculate_time_to_close(closed_issues),
            "issue_age": self._calculate_issue_age(open_issues),
        }

    async def _get_change_request_metrics(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> Dict[str, Any]:
        """CHAOSS: Change Request Closure Ratio — merged vs closed-without-merge."""
        closed_prs = await self._get_closed_pull_requests(client, owner, repo, limit=50)
        if not closed_prs:
            return {"closure_ratio": {"total": 0, "merged": 0, "closed_without_merge": 0, "ratio": 0, "score": 0}}

        merged = sum(1 for pr in closed_prs if pr.get("merged_at"))
        closed_without_merge = len(closed_prs) - merged
        ratio = (merged / len(closed_prs)) * 100

        logger.info(f"  PRs merged: {merged}/{len(closed_prs)} ({ratio:.1f}%)")
        return {
            "closure_ratio": {
                "total": len(closed_prs),
                "merged": merged,
                "closed_without_merge": closed_without_merge,
                "ratio": round(ratio, 2),
                "score": round(ratio, 2),
            }
        }

    async def _get_release_frequency(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> Dict[str, Any]:
        """CHAOSS: Release Frequency — cadence of new releases."""
        url = f"https://api.github.com/repos/{owner}/{repo}/releases"
        try:
            response = await client.get(url, headers=self.github_headers, params={"per_page": 30})
            if response.status_code != 200:
                return {}
            releases = response.json()
            if not releases:
                return {"total_releases": 0, "recent_releases": 0, "avg_days_between_releases": 0, "latest_release": None, "score": 0}

            now = datetime.now(timezone.utc)
            one_year_ago = now - timedelta(days=365)
            recent_releases = [r for r in releases if self._parse_date(r.get("published_at")) and self._parse_date(r.get("published_at")) > one_year_ago]

            avg_days = 0.0
            if len(releases) >= 2:
                release_dates = sorted(
                    filter(None, (self._parse_date(r.get("published_at")) for r in releases[:10])),
                    reverse=True,
                )
                if len(release_dates) >= 2:
                    gaps = [(release_dates[i] - release_dates[i + 1]).days for i in range(len(release_dates) - 1)]
                    avg_days = mean(gaps)

            freq_score = _bracket_score(avg_days, _RELEASE_FREQ_BRACKETS, _RELEASE_FREQ_DEFAULT_SCORE) if avg_days else 0
            latest = releases[0]
            logger.info(f"  {len(recent_releases)} releases in last year, avg {avg_days:.0f} days between")
            return {
                "total_releases": len(releases),
                "recent_releases": len(recent_releases),
                "avg_days_between_releases": round(avg_days, 1),
                "latest_release": {"tag": latest.get("tag_name"), "published_at": latest.get("published_at")},
                "score": freq_score,
            }
        except Exception as e:
            logger.error(f"Error getting releases: {e}")
            return {}

    async def _get_issues_inclusivity(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> Dict[str, Any]:
        """CHAOSS: Issues Inclusivity — unique author count across recent issues.

        Limitation: this is a rough proxy. It counts distinct issue authors as
        an indicator of participation breadth, not demographic diversity or
        depth of engagement.
        """
        issues = await self._get_recent_issues_with_comments(client, owner, repo, limit=30)
        if not issues:
            return {"total_issues": 0, "unique_participants": 0, "avg_participants_per_issue": 0, "score": 0}

        all_participants: set = set()
        participants_per_issue = []
        for issue in issues:
            login = issue.get("user", {}).get("login")
            if login:
                all_participants.add(login)
            # comments_count + 1 (author) gives a rough participation estimate per issue
            participants_per_issue.append(min(issue.get("comments", 0) + 1, 10))

        unique_count = len(all_participants)
        avg_participants = mean(participants_per_issue) if participants_per_issue else 0
        inclusivity_score = min(100, (unique_count / len(issues)) * 100)

        logger.info(f"  {unique_count} unique participants across {len(issues)} issues")
        return {
            "total_issues": len(issues),
            "unique_participants": unique_count,
            "avg_participants_per_issue": round(avg_participants, 1),
            "score": round(inclusivity_score, 2),
        }

    # ------------------------------------------------------------------ #
    # HTTP helpers                                                         #
    # ------------------------------------------------------------------ #

    async def _get_readme_content(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> Optional[Dict[str, Any]]:
        url = f"https://api.github.com/repos/{owner}/{repo}/readme"
        try:
            response = await client.get(url, headers=self.github_headers)
            if response.status_code == 200:
                data = response.json()
                content = base64.b64decode(data.get("content", "")).decode("utf-8", errors="ignore")
                return {"size": data.get("size", 0), "content": content}
            return None
        except Exception:
            return None

    async def _check_wiki_enabled(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> bool:
        url = f"https://api.github.com/repos/{owner}/{repo}"
        try:
            response = await client.get(url, headers=self.github_headers)
            return response.status_code == 200 and response.json().get("has_wiki", False)
        except Exception:
            return False

    async def _get_closed_issues(
        self, client: httpx.AsyncClient, owner: str, repo: str, limit: int = 30
    ) -> List[Dict[str, Any]]:
        url = f"https://api.github.com/repos/{owner}/{repo}/issues"
        try:
            response = await client.get(
                url, headers=self.github_headers,
                params={"state": "closed", "per_page": limit, "sort": "updated", "direction": "desc"},
            )
            if response.status_code == 200:
                return [i for i in response.json() if "pull_request" not in i]
        except Exception:
            pass
        return []

    async def _get_open_issues(
        self, client: httpx.AsyncClient, owner: str, repo: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        url = f"https://api.github.com/repos/{owner}/{repo}/issues"
        try:
            response = await client.get(
                url, headers=self.github_headers,
                params={"state": "open", "per_page": limit},
            )
            if response.status_code == 200:
                return [i for i in response.json() if "pull_request" not in i]
        except Exception:
            pass
        return []

    async def _get_closed_pull_requests(
        self, client: httpx.AsyncClient, owner: str, repo: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
        try:
            response = await client.get(
                url, headers=self.github_headers,
                params={"state": "closed", "per_page": limit, "sort": "updated", "direction": "desc"},
            )
            if response.status_code == 200:
                return response.json()
        except Exception:
            pass
        return []

    async def _get_recent_issues_with_comments(
        self, client: httpx.AsyncClient, owner: str, repo: str, limit: int = 30
    ) -> List[Dict[str, Any]]:
        url = f"https://api.github.com/repos/{owner}/{repo}/issues"
        try:
            response = await client.get(
                url, headers=self.github_headers,
                params={"state": "all", "per_page": limit, "sort": "updated", "direction": "desc"},
            )
            if response.status_code == 200:
                return [i for i in response.json() if "pull_request" not in i]
        except Exception:
            pass
        return []

    # ------------------------------------------------------------------ #
    # Pure computation helpers                                             #
    # ------------------------------------------------------------------ #

    def _assess_readme_quality(self, content: str) -> float:
        """Score README quality 0–100 based on presence of key sections."""
        if not content:
            return 0.0
        score = 0
        sections = {
            "installation": r"(?i)(install|setup|getting started)",
            "usage": r"(?i)(usage|how to|example|quick start)",
            "contributing": r"(?i)(contribut|development)",
            "license": r"(?i)(license|copyright)",
            "description": r"(?i)(description|about|overview|introduction)",
        }
        for pattern in sections.values():
            if re.search(pattern, content):
                score += 20
        if len(content) > 1000:
            score = min(100, score + 10)
        if re.search(r"!\[.*?\]\(.*?\)", content):
            score = min(100, score + 10)
        return float(min(100, score))

    def _calculate_time_to_close(self, closed_issues: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Compute time-to-close statistics from a list of closed issues."""
        if not closed_issues:
            return {"count": 0, "avg_days": 0, "median_days": 0, "min_days": 0, "max_days": 0, "score": 0}

        days_to_close = []
        for issue in closed_issues:
            created = self._parse_date(issue.get("created_at"))
            closed = self._parse_date(issue.get("closed_at"))
            if created and closed:
                days_to_close.append((closed - created).days)

        if not days_to_close:
            return {"count": 0, "avg_days": 0, "median_days": 0, "score": 0}

        avg = mean(days_to_close)
        logger.info(f"  Avg time to close: {avg:.1f} days (median: {median(days_to_close):.1f})")
        return {
            "count": len(days_to_close),
            "avg_days": round(avg, 1),
            "median_days": round(median(days_to_close), 1),
            "min_days": min(days_to_close),
            "max_days": max(days_to_close),
            "score": _bracket_score(avg, _TIME_CLOSE_BRACKETS, _TIME_CLOSE_DEFAULT_SCORE),
        }

    def _calculate_issue_age(self, open_issues: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Compute age distribution of open issues."""
        if not open_issues:
            return {"count": 0, "avg_days": 0, "median_days": 0, "max_days": 0, "stale_issues": 0, "score": 0}

        now = datetime.now(timezone.utc)
        ages = []
        for issue in open_issues:
            created = self._parse_date(issue.get("created_at"))
            if created:
                ages.append((now - created).days)

        if not ages:
            return {"count": 0, "avg_days": 0, "score": 0}

        avg = mean(ages)
        stale = sum(1 for age in ages if age > _STALE_ISSUE_DAYS)
        logger.info(f"  Avg issue age: {avg:.1f} days, {stale} stale issues")
        return {
            "count": len(ages),
            "avg_days": round(avg, 1),
            "median_days": round(median(ages), 1),
            "max_days": max(ages),
            "stale_issues": stale,
            "stale_percentage": round((stale / len(ages)) * 100, 1),
            "score": _bracket_score(avg, _ISSUE_AGE_BRACKETS, _ISSUE_AGE_DEFAULT_SCORE),
        }

    def _calculate_overall_score(
        self,
        popularity: Dict[str, Any],
        documentation: Dict[str, Any],
        issue_metrics: Dict[str, Any],
        pr_metrics: Dict[str, Any],
        release_freq: Dict[str, Any],
        inclusivity: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Compute weighted overall CHAOSS health score (0–100)."""
        pop_score = popularity.get("score", 0)
        doc_score = documentation.get("score", 0)
        time_close_score = issue_metrics.get("time_to_close", {}).get("score", 0)
        issue_age_score = issue_metrics.get("issue_age", {}).get("score", 0)
        pr_score = pr_metrics.get("closure_ratio", {}).get("score", 0)
        release_score = release_freq.get("score", 0)
        incl_score = inclusivity.get("score", 0)

        weighted_score = (
            pop_score * 0.15
            + doc_score * 0.20
            + time_close_score * 0.15
            + issue_age_score * 0.10
            + pr_score * 0.15
            + release_score * 0.15
            + incl_score * 0.10
        )

        if weighted_score >= 80:
            status = "excellent"
        elif weighted_score >= 60:
            status = "good"
        elif weighted_score >= 40:
            status = "fair"
        elif weighted_score >= 20:
            status = "poor"
        else:
            status = "critical"

        return {
            "score": round(weighted_score, 2),
            "max_score": 100,
            "status": status,
            "category_scores": {
                "project_popularity": pop_score,
                "documentation_usability": doc_score,
                "time_to_close": time_close_score,
                "issue_age": issue_age_score,
                "change_request_closure_ratio": pr_score,
                "release_frequency": release_score,
                "issues_inclusivity": incl_score,
            },
        }

    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        """Parse an ISO 8601 date string into a timezone-aware datetime."""
        if not date_str:
            return None
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except Exception:
            return None

    def _empty_result(self, repo_name: str) -> Dict[str, Any]:
        return {
            "package_name": repo_name,
            "repository": "unknown",
            "timestamp": self._get_timestamp(),
            "chaoss_metrics": {},
            "overall_score": {"score": 0, "max_score": 100, "status": "error"},
            "assessment_method": "error",
        }
