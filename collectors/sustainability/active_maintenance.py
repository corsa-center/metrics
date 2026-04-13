"""
Active Maintenance Metrics Collector (CASS Report Section 4.2.3)

Measures project activity levels, maintenance status, and indicators
of project abandonment or transition to maintenance-only mode.

Key metrics:
- Commit activity pattern (frequency, recency, gaps)
- Release pattern (frequency, recency, versioning)
- Maintenance mode indicators (archived, seeking maintainer)
- Contributor concentration (bus factor risk)
"""

import asyncio
import httpx
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


class ActiveMaintenanceCollector:
    """Collects active maintenance metrics from GitHub repositories"""

    def __init__(self, github_token: Optional[str] = None):
        self.github_token = github_token
        self.headers = {"Accept": "application/vnd.github.v3+json"}
        if github_token:
            self.headers["Authorization"] = f"token {github_token}"

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        """Collect active maintenance metrics for a package."""
        repo_name = package.get("name", "Unknown")
        repo_url = package.get("repo_url", "")

        logger.info(f"Collecting active maintenance metrics for {repo_name}")

        owner_repo = self._extract_owner_repo(repo_url)
        if not owner_repo:
            logger.error(f"Could not extract owner/repo from {repo_url}")
            return self._empty_result(repo_name)

        owner, repo = owner_repo

        # Collect all data concurrently
        (
            repo_info,
            commit_activity,
            releases,
            contributors,
        ) = await asyncio.gather(
            self._get_repo_info(owner, repo),
            self._get_commit_activity(owner, repo),
            self._get_releases(owner, repo),
            self._get_contributors(owner, repo),
            return_exceptions=True,
        )

        # Handle exceptions
        if isinstance(repo_info, Exception):
            logger.error(f"Repo info fetch failed: {repo_info}")
            repo_info = {}
        if isinstance(commit_activity, Exception):
            logger.error(f"Commit activity fetch failed: {commit_activity}")
            commit_activity = {}
        if isinstance(releases, Exception):
            logger.error(f"Releases fetch failed: {releases}")
            releases = []
        if isinstance(contributors, Exception):
            logger.error(f"Contributors fetch failed: {contributors}")
            contributors = []

        # Analyze
        maintenance_indicators = self._analyze_maintenance_indicators(repo_info)
        commit_analysis = self._analyze_commits(commit_activity)
        release_analysis = self._analyze_releases(releases)
        contributor_analysis = self._analyze_contributors(contributors)

        # Calculate score
        score = self._calculate_score(
            maintenance_indicators, commit_analysis, release_analysis, contributor_analysis
        )

        return {
            "package_name": repo_name,
            "repository": f"{owner}/{repo}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "maintenance_indicators": maintenance_indicators,
            "commit_activity": commit_analysis,
            "release_activity": release_analysis,
            "contributor_activity": contributor_analysis,
            "score": score,
        }

    async def _get_repo_info(self, owner: str, repo: str) -> Dict:
        """Get basic repository info (archived status, description, pushed_at)."""
        url = f"https://api.github.com/repos/{owner}/{repo}"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers=self.headers)
                if resp.status_code == 200:
                    return resp.json()
        except Exception as e:
            logger.debug(f"Error fetching repo info: {e}")
        return {}

    async def _get_commit_activity(self, owner: str, repo: str) -> Dict:
        """Get commit activity stats from the GitHub stats API."""
        # Get participation stats (last 52 weeks of commit counts)
        url = f"https://api.github.com/repos/{owner}/{repo}/stats/participation"
        participation = {}
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers=self.headers)
                if resp.status_code == 200:
                    participation = resp.json()
                elif resp.status_code == 202:
                    # GitHub is computing stats, wait and retry once
                    await asyncio.sleep(3)
                    resp = await client.get(url, headers=self.headers)
                    if resp.status_code == 200:
                        participation = resp.json()
        except Exception as e:
            logger.debug(f"Error fetching participation stats: {e}")

        # Get recent commits (last page to find most recent)
        url = f"https://api.github.com/repos/{owner}/{repo}/commits?per_page=1"
        last_commit = {}
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers=self.headers)
                if resp.status_code == 200:
                    data = resp.json()
                    if data:
                        last_commit = data[0]
        except Exception as e:
            logger.debug(f"Error fetching recent commits: {e}")

        return {"participation": participation, "last_commit": last_commit}

    async def _get_releases(self, owner: str, repo: str) -> List[Dict]:
        """Get recent releases."""
        url = f"https://api.github.com/repos/{owner}/{repo}/releases?per_page=20"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers=self.headers)
                if resp.status_code == 200:
                    return resp.json()
        except Exception as e:
            logger.debug(f"Error fetching releases: {e}")
        return []

    async def _get_contributors(self, owner: str, repo: str) -> List[Dict]:
        """Get top contributors."""
        url = f"https://api.github.com/repos/{owner}/{repo}/contributors?per_page=30"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers=self.headers)
                if resp.status_code == 200:
                    return resp.json()
        except Exception as e:
            logger.debug(f"Error fetching contributors: {e}")
        return []

    def _analyze_maintenance_indicators(self, repo_info: Dict) -> Dict:
        """Check for maintenance mode indicators."""
        archived = repo_info.get("archived", False)
        description = (repo_info.get("description") or "").lower()
        pushed_at = repo_info.get("pushed_at")

        # Check description for maintenance signals
        maintenance_keywords = [
            "unmaintained", "archived", "deprecated", "no longer maintained",
            "seeking maintainer", "looking for maintainer", "maintenance mode",
            "end of life", "eol", "abandoned",
        ]
        maintenance_signals = [kw for kw in maintenance_keywords if kw in description]

        # Days since last push
        days_since_push = None
        if pushed_at:
            pushed_dt = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
            days_since_push = (datetime.now(timezone.utc) - pushed_dt).days

        return {
            "archived": archived,
            "maintenance_signals": maintenance_signals,
            "days_since_last_push": days_since_push,
        }

    def _analyze_commits(self, commit_data: Dict) -> Dict:
        """Analyze commit activity patterns."""
        participation = commit_data.get("participation", {})
        last_commit = commit_data.get("last_commit", {})

        # Weekly commit counts for last 52 weeks
        all_weeks = participation.get("all", [])
        owner_weeks = participation.get("owner", [])

        if not all_weeks:
            return {
                "total_commits_52w": 0,
                "avg_commits_per_week": 0.0,
                "active_weeks_52w": 0,
                "last_commit_date": None,
                "days_since_last_commit": None,
                "recent_trend": "unknown",
            }

        total_52w = sum(all_weeks)
        active_weeks = sum(1 for w in all_weeks if w > 0)
        avg_per_week = total_52w / len(all_weeks) if all_weeks else 0

        # Trend: compare last 13 weeks vs previous 13 weeks
        if len(all_weeks) >= 26:
            recent_13 = sum(all_weeks[-13:])
            prev_13 = sum(all_weeks[-26:-13])
            if prev_13 > 0:
                trend_ratio = recent_13 / prev_13
                if trend_ratio > 1.2:
                    trend = "increasing"
                elif trend_ratio < 0.5:
                    trend = "declining"
                else:
                    trend = "stable"
            elif recent_13 > 0:
                trend = "increasing"
            else:
                trend = "inactive"
        else:
            trend = "unknown"

        # Last commit date
        last_date = None
        days_since = None
        commit_info = last_commit.get("commit", {})
        committer = commit_info.get("committer", {})
        if committer.get("date"):
            last_date = committer["date"]
            last_dt = datetime.fromisoformat(last_date.replace("Z", "+00:00"))
            days_since = (datetime.now(timezone.utc) - last_dt).days

        return {
            "total_commits_52w": total_52w,
            "avg_commits_per_week": round(avg_per_week, 1),
            "active_weeks_52w": active_weeks,
            "last_commit_date": last_date,
            "days_since_last_commit": days_since,
            "recent_trend": trend,
        }

    def _analyze_releases(self, releases: List[Dict]) -> Dict:
        """Analyze release patterns."""
        if not releases:
            return {
                "total_releases": 0,
                "latest_release": None,
                "days_since_latest_release": None,
                "releases_last_year": 0,
                "uses_semver": False,
            }

        latest = releases[0]
        latest_date = latest.get("published_at") or latest.get("created_at")

        days_since_latest = None
        if latest_date:
            latest_dt = datetime.fromisoformat(latest_date.replace("Z", "+00:00"))
            days_since_latest = (datetime.now(timezone.utc) - latest_dt).days

        # Count releases in last year
        one_year_ago = datetime.now(timezone.utc) - timedelta(days=365)
        releases_last_year = 0
        for r in releases:
            pub = r.get("published_at") or r.get("created_at")
            if pub:
                pub_dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                if pub_dt >= one_year_ago:
                    releases_last_year += 1

        # Check semver pattern
        semver_pattern = re.compile(r"v?\d+\.\d+(\.\d+)?")
        uses_semver = any(
            semver_pattern.match(r.get("tag_name", "")) for r in releases[:5]
        )

        return {
            "total_releases": len(releases),
            "latest_release": latest.get("tag_name"),
            "latest_release_date": latest_date,
            "days_since_latest_release": days_since_latest,
            "releases_last_year": releases_last_year,
            "uses_semver": uses_semver,
        }

    def _analyze_contributors(self, contributors: List[Dict]) -> Dict:
        """Analyze contributor concentration (bus factor)."""
        if not contributors:
            return {
                "total_contributors": 0,
                "top_contributor_pct": 0,
                "bus_factor": 0,
            }

        total_contribs = sum(c.get("contributions", 0) for c in contributors)
        if total_contribs == 0:
            return {
                "total_contributors": len(contributors),
                "top_contributor_pct": 0,
                "bus_factor": 0,
            }

        # Bus factor: minimum contributors needed for >50% of commits
        sorted_contribs = sorted(
            contributors, key=lambda c: c.get("contributions", 0), reverse=True
        )
        cumulative = 0
        bus_factor = 0
        for c in sorted_contribs:
            cumulative += c.get("contributions", 0)
            bus_factor += 1
            if cumulative > total_contribs * 0.5:
                break

        top_pct = round(
            sorted_contribs[0].get("contributions", 0) / total_contribs * 100, 1
        )

        return {
            "total_contributors": len(contributors),
            "top_contributor_pct": top_pct,
            "bus_factor": bus_factor,
        }

    def _calculate_score(
        self, indicators: Dict, commits: Dict, releases: Dict, contributors: Dict
    ) -> Dict:
        """Calculate active maintenance score."""
        score = 0
        max_score = 5
        details = []

        # 1. Not archived / no abandonment signals
        if not indicators.get("archived") and not indicators.get("maintenance_signals"):
            score += 1
            details.append("Project active (not archived): \u2713")
        else:
            if indicators.get("archived"):
                details.append("Project archived: \u2717")
            else:
                details.append(f"Maintenance signals: {', '.join(indicators['maintenance_signals'])}")

        # 2. Recent commit activity (within 90 days)
        days = commits.get("days_since_last_commit")
        if days is not None and days <= 90:
            score += 1
            details.append(f"Recent commits ({days} days ago): \u2713")
        elif days is not None:
            details.append(f"Last commit {days} days ago: \u2717")
        else:
            details.append("Commit activity: unknown")

        # 3. Sustained activity (>= 26 active weeks in last year)
        active_weeks = commits.get("active_weeks_52w", 0)
        if active_weeks >= 26:
            score += 1
            details.append(f"Sustained activity ({active_weeks}/52 active weeks): \u2713")
        else:
            details.append(f"Activity level ({active_weeks}/52 active weeks): \u2717")

        # 4. Release activity (at least 1 release in last year)
        releases_ly = releases.get("releases_last_year", 0)
        if releases_ly >= 1:
            score += 1
            details.append(f"Recent releases ({releases_ly} in last year): \u2713")
        else:
            details.append("No releases in last year: \u2717")

        # 5. Bus factor >= 3 (not overly dependent on one person)
        bus = contributors.get("bus_factor", 0)
        if bus >= 3:
            score += 1
            details.append(f"Healthy bus factor ({bus} contributors for 50% of work): \u2713")
        else:
            details.append(f"Bus factor risk ({bus} contributor(s) for 50% of work): \u2717")

        return {
            "score": score,
            "max_score": max_score,
            "percentage": round((score / max_score) * 100, 2),
            "details": details,
        }

    def _extract_owner_repo(self, repo_url: str) -> Optional[tuple]:
        patterns = [
            r"github\.com/([^/]+)/([^/]+)",
            r"github\.com:([^/]+)/([^/]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, repo_url)
            if match:
                owner = match.group(1)
                repo = match.group(2).replace(".git", "")
                return (owner, repo)
        return None

    def _empty_result(self, repo_name: str) -> Dict:
        return {
            "package_name": repo_name,
            "repository": "unknown",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "maintenance_indicators": {},
            "commit_activity": {},
            "release_activity": {},
            "contributor_activity": {},
            "score": {"score": 0, "max_score": 5, "percentage": 0, "details": []},
        }
