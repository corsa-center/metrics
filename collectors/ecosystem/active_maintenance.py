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

from forge.base import RetryingTransport
from forge.interface import Forge

logger = logging.getLogger(__name__)

# Community channels a project might link from its README, beyond the tracker.
_CHANNEL_PATTERNS = {
    "Mailing list": re.compile(r"mailing[- ]list|listserv|groups\.google\.com|majordomo|\bmailman\b", re.I),
    "Chat (Slack/Discord/Matrix)": re.compile(r"slack\.com|discord\.(?:gg|com)|matrix\.to|gitter\.im|zulipchat", re.I),
    "Forum": re.compile(r"\bforum\b|discourse\.|stackoverflow\.com/questions/tagged", re.I),
    "Help desk": re.compile(r"help ?desk|support portal|jira|servicedesk", re.I),
}

# The pass/fail thresholds for this collector's data (departure rate,
# channel count, release cadence, etc.) live in config/thresholds.yaml under
# section "4.2.3" and are applied in orchestrator.py's dashboard rendering,
# not here -- this collector only gathers the underlying numbers.


class ActiveMaintenanceCollector:
    """Collects active maintenance metrics from GitHub repositories"""

    def __init__(self, forge: Forge):
        self.forge = forge

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        """Collect active maintenance metrics for a package."""
        repo_name = package.get("name", "Unknown")
        repo_url = package.get("repo_url", "")

        logger.info(f"Collecting active maintenance metrics for {repo_name}")

        ref = self.forge.extract_ref(repo_url)
        if not ref:
            logger.error(f"Could not extract a repo reference from {repo_url}")
            return self._empty_result(repo_name)

        async with httpx.AsyncClient(timeout=60.0, transport=RetryingTransport()) as client:
            # Collect all data concurrently
            (
                repo_info,
                commit_activity,
                releases,
                contributors,
                first_commit_date,
                contributor_stats,
                readme_text,
            ) = await asyncio.gather(
                self._get_repo_info(client, ref),
                self._get_commit_activity(client, ref),
                self._get_releases(client, ref),
                self._get_contributors(client, ref),
                self.forge.first_commit_date(client, ref),
                self.forge.contributor_weekly_stats(client, ref),
                self._get_readme(client, ref),
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
        if isinstance(first_commit_date, Exception):
            logger.error(f"First commit fetch failed: {first_commit_date}")
            first_commit_date = None
        if isinstance(contributor_stats, Exception):
            logger.error(f"Contributor stats fetch failed: {contributor_stats}")
            contributor_stats = []
        if isinstance(readme_text, Exception):
            logger.error(f"README fetch failed: {readme_text}")
            readme_text = ""

        # Analyze
        maintenance_indicators = self._analyze_maintenance_indicators(repo_info, first_commit_date)
        commit_analysis = self._analyze_commits(commit_activity)
        release_analysis = self._analyze_releases(releases)
        contributor_analysis = self._analyze_contributors(contributors)
        abandonment = self._analyze_abandonment(contributor_stats)
        channels = self._analyze_channels(repo_info, readme_text)

        # Calculate score
        score = self._calculate_score(
            maintenance_indicators, commit_analysis, release_analysis, contributor_analysis
        )

        return {
            "package_name": repo_name,
            "repository": ref,
            "timestamp": self.forge.get_timestamp(),
            "maintenance_indicators": maintenance_indicators,
            "commit_activity": commit_analysis,
            "release_activity": release_analysis,
            "contributor_activity": contributor_analysis,
            "abandonment": abandonment,
            "channels": channels,
            "score": score,
        }

    async def _get_readme(self, client: httpx.AsyncClient, ref: str) -> str:
        """README text, used to find community channels linked from it."""
        text = await self.forge.readme(client, ref)
        return text or ""

    def _analyze_abandonment(self, stats: List[Dict]) -> Dict:
        """Contributors who were active last year but have since gone quiet.

        Compares the two most recent 52-week windows per contributor. Someone
        who committed in the earlier window and not the later one has stopped;
        a high share of those is the abandonment signal the report asks for.
        """
        if not stats:
            return {"measurable": False, "previously_active": 0,
                    "departed": 0, "departure_rate": None}

        previously_active, departed = 0, 0
        for contributor in stats:
            weeks = contributor.get("weeks") or []
            if len(weeks) < 104:
                continue
            prior = sum(w.get("c", 0) for w in weeks[-104:-52])
            recent = sum(w.get("c", 0) for w in weeks[-52:])
            if prior > 0:
                previously_active += 1
                if recent == 0:
                    departed += 1

        if previously_active == 0:
            return {"measurable": False, "previously_active": 0,
                    "departed": 0, "departure_rate": None}

        return {
            "measurable": True,
            "previously_active": previously_active,
            "departed": departed,
            "departure_rate": round(departed / previously_active, 3),
        }

    def _analyze_channels(self, repo_info: Dict, readme: str) -> Dict:
        """Community channels the project runs, beyond the issue tracker."""
        found = []
        if repo_info.get("has_discussions"):
            found.append("GitHub Discussions")
        if repo_info.get("has_wiki"):
            found.append("Wiki")

        for label, pattern in _CHANNEL_PATTERNS.items():
            if readme and pattern.search(readme):
                found.append(label)
        return {"found": found, "count": len(found)}

    async def _get_repo_info(self, client: httpx.AsyncClient, ref: str) -> Dict:
        """Get basic repository info (archived status, description, pushed_at)."""
        data = await self.forge.repo_info(client, ref)
        return data if data else {}

    async def _get_commit_activity(self, client: httpx.AsyncClient, ref: str) -> Dict:
        """Get commit activity stats: 52-week participation plus the most recent commit."""
        participation = await self.forge.commit_participation(client, ref)
        commits = await self.forge.commits(client, ref, per_page=1)
        last_commit = commits[0] if commits else {}
        return {"participation": participation, "last_commit": last_commit}

    async def _get_releases(self, client: httpx.AsyncClient, ref: str) -> List[Dict]:
        """Get recent releases."""
        data = await self.forge.releases(client, ref, per_page=20)
        return data if data else []

    async def _get_contributors(self, client: httpx.AsyncClient, ref: str) -> List[Dict]:
        """Get contributors, paginated.

        Bounded to 5 pages (500 contributors) to cap worst-case API calls —
        far more than the portfolio's projects have, but avoids unbounded
        pagination for an unusually large repository.
        """
        contributors: List[Dict] = []
        max_pages = 5
        for page in range(1, max_pages + 1):
            batch = await self.forge.contributors(client, ref, per_page=100, page=page)
            if not batch:
                break
            contributors.extend(batch)
        return contributors

    def _analyze_maintenance_indicators(
        self, repo_info: Dict, first_commit_date: Optional[str] = None
    ) -> Dict:
        """Check for maintenance mode indicators."""
        archived = repo_info.get("archived", False)
        description = (repo_info.get("description") or "").lower()
        pushed_at = repo_info.get("pushed_at")
        created_at = repo_info.get("created_at")

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

        # Two different ages, deliberately reported separately.
        #
        #   repo_age_years    — since the GitHub repository was created. Understates
        #                       projects that migrated from another VCS: HDF5's repo
        #                       dates to 2020, the software to the 1990s.
        #   history_age_years — since the oldest commit. Captures imported history,
        #                       but a repo created empty can have its first commit
        #                       land *after* creation (zfp, by a day), and a
        #                       history rewrite resets it.
        #
        # project_age_years takes the longer of the two as the best available
        # estimate, and both source dates are kept so the figure can be checked.
        now = datetime.now(timezone.utc)

        def _years_since(date_str: Optional[str]) -> Optional[float]:
            if not date_str:
                return None
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return round((now - dt).days / 365.25, 1)

        repo_age_years = _years_since(created_at)
        history_age_years = _years_since(first_commit_date)
        known_ages = [a for a in (repo_age_years, history_age_years) if a is not None]
        project_age_years = max(known_ages) if known_ages else None

        return {
            "archived": archived,
            "maintenance_signals": maintenance_signals,
            "days_since_last_push": days_since_push,
            "created_at": created_at,
            "first_commit_date": first_commit_date,
            "repo_age_years": repo_age_years,
            "history_age_years": history_age_years,
            "project_age_years": project_age_years,
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
        if last_commit.get("committer_date"):
            last_date = last_commit["committer_date"]
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

        total_contribs = sum(c.get("commit_count", 0) for c in contributors)
        if total_contribs == 0:
            return {
                "total_contributors": len(contributors),
                "top_contributor_pct": 0,
                "bus_factor": 0,
            }

        # Bus factor: minimum contributors needed for >50% of commits
        sorted_contribs = sorted(
            contributors, key=lambda c: c.get("commit_count", 0), reverse=True
        )
        cumulative = 0
        bus_factor = 0
        for c in sorted_contribs:
            cumulative += c.get("commit_count", 0)
            bus_factor += 1
            if cumulative > total_contribs * 0.5:
                break

        top_pct = round(
            sorted_contribs[0].get("commit_count", 0) / total_contribs * 100, 1
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

    def _empty_result(self, repo_name: str) -> Dict:
        return {
            "package_name": repo_name,
            "repository": "unknown",
            "timestamp": self.forge.get_timestamp(),
            "maintenance_indicators": {},
            "commit_activity": {},
            "release_activity": {},
            "contributor_activity": {},
            "score": {"score": 0, "max_score": 5, "percentage": 0, "details": []},
        }
