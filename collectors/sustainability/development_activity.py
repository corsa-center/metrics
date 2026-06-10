"""
Development Activity Collector

Analyzes repository development activity and community engagement including:
- Commit history patterns and contributor activity
- Release frequency and versioning practices
- Issue resolution patterns and response times
- Community engagement indicators (PRs, discussions, responsiveness)

Metrics Covered:
- commit_activity_score: Commit frequency, patterns, and contributor diversity
- release_cadence_score: Release frequency and version management
- issue_resolution_score: Issue handling efficiency and response times
- community_engagement_score: PR activity, contributor growth, responsiveness
"""

import asyncio
import logging
import re
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timedelta
from collections import defaultdict
import httpx

logger = logging.getLogger(__name__)


class DevelopmentActivityCollector:
    """
    Collects development activity metrics by analyzing repository
    commit history, releases, issues, and community engagement.
    """

    # Time periods for analysis
    ANALYSIS_PERIODS = {
        "recent": 90,      # Last 90 days
        "medium": 180,     # Last 6 months
        "extended": 365,   # Last year
    }

    # Commit message quality patterns
    COMMIT_PATTERNS = {
        "conventional": [
            r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\(.+\))?:",
            r"^(BREAKING CHANGE|DEPRECATED):",
        ],
        "semantic": [
            r"^(Add|Fix|Update|Remove|Refactor|Improve|Implement|Create|Delete|Merge)",
        ],
        "issue_reference": [
            r"#\d+",
            r"(closes?|fixes?|resolves?)\s+#\d+",
            r"(GH-|ISSUE-|BUG-)\d+",
        ],
        "low_quality": [
            r"^(wip|WIP|temp|tmp|test|fix|update|changes?)$",
            r"^\.+$",
            r"^\s*$",
        ],
    }

    # Semantic versioning pattern
    SEMVER_PATTERN = r"v?(\d+)\.(\d+)\.(\d+)(?:-([a-zA-Z0-9.-]+))?(?:\+([a-zA-Z0-9.-]+))?"

    def __init__(self, github_token: Optional[str] = None):
        """Initialize the collector with optional GitHub token."""
        self.github_token = github_token
        self.github_headers = {}
        if github_token:
            self.github_headers = {
                "Authorization": f"token {github_token}",
                "Accept": "application/vnd.github.v3+json",
            }

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        """
        Main entry point - collects all development activity metrics.

        Args:
            package: Dictionary containing package info with 'repo_url' key

        Returns:
            Dictionary with all collected metrics and scores
        """
        repo_url = package.get("repo_url", "")
        repo_name = package.get("name", "unknown")

        logger.info(f"Collecting development activity metrics for {repo_name}")

        owner_repo = self._extract_owner_repo(repo_url)
        if not owner_repo:
            logger.warning(f"Could not extract owner/repo from URL: {repo_url}")
            return self._empty_result(repo_name)

        owner, repo = owner_repo

        try:
            # Collect all metrics concurrently
            results = await asyncio.gather(
                self._analyze_commit_activity(owner, repo),
                self._analyze_release_cadence(owner, repo),
                self._analyze_issue_resolution(owner, repo),
                self._analyze_community_engagement(owner, repo),
                return_exceptions=True,
            )

            # Handle exceptions
            commit_activity = results[0] if not isinstance(results[0], Exception) else self._empty_commits()
            release_cadence = results[1] if not isinstance(results[1], Exception) else self._empty_releases()
            issue_resolution = results[2] if not isinstance(results[2], Exception) else self._empty_issues()
            community = results[3] if not isinstance(results[3], Exception) else self._empty_community()

            # Log exceptions
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"Error in activity analysis {i}: {result}")

            # Calculate overall score
            overall_score = self._calculate_overall_score(
                commit_activity, release_cadence, issue_resolution, community
            )

            return {
                "package_name": repo_name,
                "repository": f"{owner}/{repo}",
                "timestamp": self._get_timestamp(),
                "commit_activity": commit_activity,
                "release_cadence": release_cadence,
                "issue_resolution": issue_resolution,
                "community_engagement": community,
                "overall_score": overall_score,
            }

        except Exception as e:
            logger.error(f"Error collecting activity metrics for {repo_name}: {e}")
            return self._empty_result(repo_name)

    # ==================== Commit Activity Analysis ====================

    async def _analyze_commit_activity(self, owner: str, repo: str) -> Dict[str, Any]:
        """
        Analyze commit history and patterns.

        Evaluates:
        - Commit frequency over time
        - Contributor diversity
        - Commit message quality
        - Activity trends
        """
        logger.debug(f"Analyzing commit activity for {owner}/{repo}")

        try:
            # Get commit statistics
            commits_data = await self._fetch_commits(owner, repo)
            if not commits_data:
                return self._empty_commits()

            # Get contributor statistics
            contributors = await self._fetch_contributors(owner, repo)

            # Analyze commits
            now = datetime.utcnow()
            recent_cutoff = now - timedelta(days=90)
            medium_cutoff = now - timedelta(days=180)
            year_cutoff = now - timedelta(days=365)

            commits_by_period = {
                "last_90_days": 0,
                "last_180_days": 0,
                "last_365_days": 0,
            }

            commits_by_author: Dict[str, int] = defaultdict(int)
            commits_by_month: Dict[str, int] = defaultdict(int)
            message_quality = {"conventional": 0, "semantic": 0, "with_issue_ref": 0, "low_quality": 0, "total": 0}

            for commit in commits_data:
                commit_date = self._parse_commit_date(commit)
                if not commit_date:
                    continue

                # Count by period
                if commit_date >= recent_cutoff:
                    commits_by_period["last_90_days"] += 1
                if commit_date >= medium_cutoff:
                    commits_by_period["last_180_days"] += 1
                if commit_date >= year_cutoff:
                    commits_by_period["last_365_days"] += 1

                # Count by author
                author = commit.get("author", {})
                if author:
                    author_login = author.get("login", "unknown")
                    commits_by_author[author_login] += 1

                # Count by month
                month_key = commit_date.strftime("%Y-%m")
                commits_by_month[month_key] += 1

                # Analyze message quality
                message = commit.get("commit", {}).get("message", "")
                self._analyze_commit_message(message, message_quality)

            # Calculate metrics
            total_commits = len(commits_data)
            total_contributors = len(contributors) if contributors else len(commits_by_author)

            # Commit frequency (commits per week in last 90 days)
            commits_per_week = commits_by_period["last_90_days"] / 13 if commits_by_period["last_90_days"] > 0 else 0

            # Contributor diversity (Gini coefficient inverse)
            contributor_diversity = self._calculate_contributor_diversity(commits_by_author)

            # Message quality ratio
            total_analyzed = message_quality["total"]
            quality_ratio = (
                (message_quality["conventional"] + message_quality["semantic"] + message_quality["with_issue_ref"])
                / max(total_analyzed, 1)
            )

            # Activity trend (comparing recent to older)
            recent_monthly = commits_by_period["last_90_days"] / 3
            older_monthly = (commits_by_period["last_180_days"] - commits_by_period["last_90_days"]) / 3
            trend = "increasing" if recent_monthly > older_monthly * 1.1 else (
                "decreasing" if recent_monthly < older_monthly * 0.9 else "stable"
            )

            # Calculate score
            frequency_score = min(100, commits_per_week * 10)  # 10 commits/week = 100
            diversity_score = contributor_diversity * 100
            quality_score = quality_ratio * 100
            trend_bonus = 10 if trend == "increasing" else (0 if trend == "stable" else -10)

            score = (frequency_score * 0.35 + diversity_score * 0.30 + quality_score * 0.25 + 10) + trend_bonus
            score = max(0, min(100, score))

            # Get recent commit activity for timeline
            recent_months = sorted(commits_by_month.items(), reverse=True)[:12]

            return {
                "score": round(score, 2),
                "total_commits_analyzed": total_commits,
                "total_contributors": total_contributors,
                "commits_by_period": commits_by_period,
                "commits_per_week": round(commits_per_week, 2),
                "contributor_diversity": round(contributor_diversity, 2),
                "commit_message_quality": {
                    "conventional_commits": message_quality["conventional"],
                    "semantic_messages": message_quality["semantic"],
                    "with_issue_reference": message_quality["with_issue_ref"],
                    "low_quality": message_quality["low_quality"],
                    "quality_ratio": round(quality_ratio, 2),
                },
                "activity_trend": trend,
                "monthly_commits": dict(recent_months),
                "top_contributors": self._get_top_contributors(commits_by_author, 10),
                "last_commit_date": self._get_last_commit_date(commits_data),
                "status": self._get_status(score),
            }

        except Exception as e:
            logger.error(f"Error analyzing commit activity: {e}")
            return self._empty_commits()

    async def _fetch_commits(self, owner: str, repo: str, per_page: int = 100) -> List[Dict]:
        """Fetch recent commits from the repository."""
        url = f"https://api.github.com/repos/{owner}/{repo}/commits"
        params = {"per_page": per_page}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=self.github_headers, params=params)
                if response.status_code == 200:
                    return response.json()
                logger.warning(f"Failed to fetch commits: {response.status_code}")
        except Exception as e:
            logger.error(f"Error fetching commits: {e}")

        return []

    async def _fetch_contributors(self, owner: str, repo: str) -> List[Dict]:
        """Fetch contributor statistics."""
        url = f"https://api.github.com/repos/{owner}/{repo}/contributors"
        params = {"per_page": 100}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=self.github_headers, params=params)
                if response.status_code == 200:
                    return response.json()
        except Exception as e:
            logger.debug(f"Error fetching contributors: {e}")

        return []

    def _parse_commit_date(self, commit: Dict) -> Optional[datetime]:
        """Parse commit date from commit data."""
        try:
            date_str = commit.get("commit", {}).get("author", {}).get("date", "")
            if date_str:
                return datetime.fromisoformat(date_str.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            pass
        return None

    def _analyze_commit_message(self, message: str, quality: Dict) -> None:
        """Analyze commit message quality."""
        quality["total"] += 1
        first_line = message.split("\n")[0]

        # Check for conventional commits
        for pattern in self.COMMIT_PATTERNS["conventional"]:
            if re.match(pattern, first_line, re.IGNORECASE):
                quality["conventional"] += 1
                break

        # Check for semantic style
        for pattern in self.COMMIT_PATTERNS["semantic"]:
            if re.match(pattern, first_line):
                quality["semantic"] += 1
                break

        # Check for issue references
        for pattern in self.COMMIT_PATTERNS["issue_reference"]:
            if re.search(pattern, message, re.IGNORECASE):
                quality["with_issue_ref"] += 1
                break

        # Check for low quality
        for pattern in self.COMMIT_PATTERNS["low_quality"]:
            if re.match(pattern, first_line):
                quality["low_quality"] += 1
                break

    def _calculate_contributor_diversity(self, commits_by_author: Dict[str, int]) -> float:
        """Calculate contributor diversity (inverse concentration)."""
        if not commits_by_author:
            return 0.0

        total = sum(commits_by_author.values())
        if total == 0:
            return 0.0

        # Calculate Herfindahl-Hirschman Index (HHI) and invert
        shares = [count / total for count in commits_by_author.values()]
        hhi = sum(s ** 2 for s in shares)

        # Invert HHI: 1/n (perfect diversity) to 1 (monopoly)
        # Normalize to 0-1 scale where 1 is most diverse
        n = len(commits_by_author)
        if n == 1:
            return 0.0

        min_hhi = 1 / n
        diversity = (1 - hhi) / (1 - min_hhi) if hhi < 1 else 0

        return diversity

    def _get_top_contributors(self, commits_by_author: Dict[str, int], limit: int) -> List[Dict]:
        """Get top contributors by commit count."""
        sorted_contributors = sorted(
            commits_by_author.items(),
            key=lambda x: x[1],
            reverse=True
        )[:limit]

        total = sum(commits_by_author.values())
        return [
            {
                "login": login,
                "commits": count,
                "percentage": round(count / total * 100, 1) if total > 0 else 0,
            }
            for login, count in sorted_contributors
        ]

    def _get_last_commit_date(self, commits: List[Dict]) -> Optional[str]:
        """Get the date of the most recent commit."""
        if commits:
            date = self._parse_commit_date(commits[0])
            if date:
                return date.isoformat()
        return None

    # ==================== Release Cadence Analysis ====================

    async def _analyze_release_cadence(self, owner: str, repo: str) -> Dict[str, Any]:
        """
        Analyze release frequency and versioning practices.

        Evaluates:
        - Release frequency
        - Time between releases
        - Semantic versioning adherence
        - Pre-release usage
        """
        logger.debug(f"Analyzing release cadence for {owner}/{repo}")

        try:
            # Fetch releases
            releases = await self._fetch_releases(owner, repo)
            tags = await self._fetch_tags(owner, repo)

            if not releases and not tags:
                return self._empty_releases()

            # Analyze releases
            now = datetime.utcnow()
            release_dates = []
            semver_releases = 0
            prerelease_count = 0
            releases_by_year: Dict[str, int] = defaultdict(int)

            for release in releases:
                # Parse release date
                published = release.get("published_at") or release.get("created_at")
                if published:
                    try:
                        date = datetime.fromisoformat(published.replace("Z", "+00:00")).replace(tzinfo=None)
                        release_dates.append(date)
                        releases_by_year[str(date.year)] += 1
                    except Exception:
                        pass

                # Check semver
                tag_name = release.get("tag_name", "")
                if re.match(self.SEMVER_PATTERN, tag_name):
                    semver_releases += 1

                # Check prerelease
                if release.get("prerelease"):
                    prerelease_count += 1

            # Calculate metrics
            total_releases = len(releases)
            release_dates.sort(reverse=True)

            # Time between releases
            release_intervals = []
            for i in range(len(release_dates) - 1):
                interval = (release_dates[i] - release_dates[i + 1]).days
                release_intervals.append(interval)

            avg_interval = sum(release_intervals) / len(release_intervals) if release_intervals else 0
            median_interval = sorted(release_intervals)[len(release_intervals) // 2] if release_intervals else 0

            # Releases in last year
            year_ago = now - timedelta(days=365)
            releases_last_year = sum(1 for d in release_dates if d >= year_ago)

            # Days since last release
            days_since_release = (now - release_dates[0]).days if release_dates else None

            # Semver adherence
            semver_ratio = semver_releases / total_releases if total_releases > 0 else 0

            # Calculate score
            # Ideal: regular releases (every 2-8 weeks), semver, recent activity
            frequency_score = min(100, releases_last_year * 10)  # 10 releases/year = 100

            recency_score = 100
            if days_since_release is not None:
                if days_since_release <= 30:
                    recency_score = 100
                elif days_since_release <= 90:
                    recency_score = 80
                elif days_since_release <= 180:
                    recency_score = 60
                elif days_since_release <= 365:
                    recency_score = 40
                else:
                    recency_score = max(0, 40 - (days_since_release - 365) / 30)

            semver_score = semver_ratio * 100

            score = frequency_score * 0.40 + recency_score * 0.35 + semver_score * 0.25
            score = max(0, min(100, score))

            return {
                "score": round(score, 2),
                "total_releases": total_releases,
                "total_tags": len(tags),
                "releases_last_year": releases_last_year,
                "average_days_between_releases": round(avg_interval, 1),
                "median_days_between_releases": median_interval,
                "days_since_last_release": days_since_release,
                "semver_compliance": {
                    "compliant_releases": semver_releases,
                    "ratio": round(semver_ratio, 2),
                },
                "prerelease_usage": {
                    "count": prerelease_count,
                    "ratio": round(prerelease_count / total_releases, 2) if total_releases > 0 else 0,
                },
                "releases_by_year": dict(sorted(releases_by_year.items(), reverse=True)),
                "latest_release": {
                    "tag": releases[0].get("tag_name") if releases else None,
                    "date": release_dates[0].isoformat() if release_dates else None,
                    "name": releases[0].get("name") if releases else None,
                },
                "release_frequency": self._categorize_release_frequency(avg_interval),
                "status": self._get_status(score),
            }

        except Exception as e:
            logger.error(f"Error analyzing release cadence: {e}")
            return self._empty_releases()

    async def _fetch_releases(self, owner: str, repo: str) -> List[Dict]:
        """Fetch releases from the repository."""
        url = f"https://api.github.com/repos/{owner}/{repo}/releases"
        params = {"per_page": 100}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=self.github_headers, params=params)
                if response.status_code == 200:
                    return response.json()
        except Exception as e:
            logger.debug(f"Error fetching releases: {e}")

        return []

    async def _fetch_tags(self, owner: str, repo: str) -> List[Dict]:
        """Fetch tags from the repository."""
        url = f"https://api.github.com/repos/{owner}/{repo}/tags"
        params = {"per_page": 100}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=self.github_headers, params=params)
                if response.status_code == 200:
                    return response.json()
        except Exception as e:
            logger.debug(f"Error fetching tags: {e}")

        return []

    def _categorize_release_frequency(self, avg_days: float) -> str:
        """Categorize release frequency."""
        if avg_days == 0:
            return "unknown"
        elif avg_days <= 7:
            return "very_frequent"  # Weekly or more
        elif avg_days <= 30:
            return "frequent"  # Monthly
        elif avg_days <= 90:
            return "regular"  # Quarterly
        elif avg_days <= 180:
            return "moderate"  # Semi-annually
        else:
            return "infrequent"  # Less than semi-annually

    # ==================== Issue Resolution Analysis ====================

    async def _analyze_issue_resolution(self, owner: str, repo: str) -> Dict[str, Any]:
        """
        Analyze issue handling and resolution patterns.

        Evaluates:
        - Issue response time
        - Resolution time
        - Close rate
        - Issue labeling practices
        """
        logger.debug(f"Analyzing issue resolution for {owner}/{repo}")

        try:
            # Fetch issues (both open and closed)
            open_issues = await self._fetch_issues(owner, repo, state="open")
            closed_issues = await self._fetch_issues(owner, repo, state="closed")

            total_open = len(open_issues)
            total_closed = len(closed_issues)
            total_issues = total_open + total_closed

            if total_issues == 0:
                return self._empty_issues()

            # Analyze closed issues for resolution time
            resolution_times = []
            first_response_times = []
            issues_with_labels = 0
            issues_by_month: Dict[str, Dict[str, int]] = defaultdict(lambda: {"opened": 0, "closed": 0})

            for issue in closed_issues:
                # Resolution time
                created = issue.get("created_at")
                closed = issue.get("closed_at")
                if created and closed:
                    try:
                        created_dt = datetime.fromisoformat(created.replace("Z", "+00:00")).replace(tzinfo=None)
                        closed_dt = datetime.fromisoformat(closed.replace("Z", "+00:00")).replace(tzinfo=None)
                        resolution_days = (closed_dt - created_dt).days
                        resolution_times.append(resolution_days)

                        # Track by month
                        month_key = created_dt.strftime("%Y-%m")
                        issues_by_month[month_key]["closed"] += 1
                    except Exception:
                        pass

                # Labels
                if issue.get("labels"):
                    issues_with_labels += 1

            # Analyze open issues
            now = datetime.utcnow()
            open_ages = []
            stale_issues = 0  # Open > 90 days with no recent activity

            for issue in open_issues:
                created = issue.get("created_at")
                updated = issue.get("updated_at")
                if created:
                    try:
                        created_dt = datetime.fromisoformat(created.replace("Z", "+00:00")).replace(tzinfo=None)
                        age = (now - created_dt).days
                        open_ages.append(age)

                        # Track by month
                        month_key = created_dt.strftime("%Y-%m")
                        issues_by_month[month_key]["opened"] += 1

                        # Check if stale
                        if updated:
                            updated_dt = datetime.fromisoformat(updated.replace("Z", "+00:00")).replace(tzinfo=None)
                            if age > 90 and (now - updated_dt).days > 30:
                                stale_issues += 1
                    except Exception:
                        pass

                if issue.get("labels"):
                    issues_with_labels += 1

            # Calculate metrics
            close_rate = total_closed / total_issues if total_issues > 0 else 0

            avg_resolution_time = sum(resolution_times) / len(resolution_times) if resolution_times else 0
            median_resolution_time = sorted(resolution_times)[len(resolution_times) // 2] if resolution_times else 0

            avg_open_age = sum(open_ages) / len(open_ages) if open_ages else 0
            label_usage = issues_with_labels / total_issues if total_issues > 0 else 0

            stale_ratio = stale_issues / total_open if total_open > 0 else 0

            # Calculate score
            close_rate_score = close_rate * 100

            resolution_score = 100
            if avg_resolution_time > 0:
                if avg_resolution_time <= 7:
                    resolution_score = 100
                elif avg_resolution_time <= 30:
                    resolution_score = 80
                elif avg_resolution_time <= 90:
                    resolution_score = 60
                else:
                    resolution_score = max(0, 60 - (avg_resolution_time - 90) / 10)

            stale_score = (1 - stale_ratio) * 100
            label_score = label_usage * 100

            score = (
                close_rate_score * 0.30 +
                resolution_score * 0.35 +
                stale_score * 0.20 +
                label_score * 0.15
            )
            score = max(0, min(100, score))

            # Recent months trend
            recent_months = sorted(issues_by_month.items(), reverse=True)[:6]

            return {
                "score": round(score, 2),
                "total_issues": total_issues,
                "open_issues": total_open,
                "closed_issues": total_closed,
                "close_rate": round(close_rate, 2),
                "resolution_time": {
                    "average_days": round(avg_resolution_time, 1),
                    "median_days": median_resolution_time,
                    "fastest": min(resolution_times) if resolution_times else None,
                    "slowest": max(resolution_times) if resolution_times else None,
                },
                "open_issue_health": {
                    "average_age_days": round(avg_open_age, 1),
                    "stale_issues": stale_issues,
                    "stale_ratio": round(stale_ratio, 2),
                },
                "labeling_practices": {
                    "issues_with_labels": issues_with_labels,
                    "label_usage_ratio": round(label_usage, 2),
                },
                "monthly_activity": dict(recent_months),
                "resolution_efficiency": self._categorize_resolution_time(avg_resolution_time),
                "status": self._get_status(score),
            }

        except Exception as e:
            logger.error(f"Error analyzing issue resolution: {e}")
            return self._empty_issues()

    async def _fetch_issues(self, owner: str, repo: str, state: str = "all") -> List[Dict]:
        """Fetch issues from the repository."""
        url = f"https://api.github.com/repos/{owner}/{repo}/issues"
        params = {"state": state, "per_page": 100, "sort": "updated", "direction": "desc"}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=self.github_headers, params=params)
                if response.status_code == 200:
                    # Filter out pull requests (they're included in issues API)
                    issues = response.json()
                    return [i for i in issues if "pull_request" not in i]
        except Exception as e:
            logger.debug(f"Error fetching issues: {e}")

        return []

    def _categorize_resolution_time(self, avg_days: float) -> str:
        """Categorize issue resolution efficiency."""
        if avg_days == 0:
            return "unknown"
        elif avg_days <= 3:
            return "excellent"
        elif avg_days <= 7:
            return "very_good"
        elif avg_days <= 14:
            return "good"
        elif avg_days <= 30:
            return "moderate"
        elif avg_days <= 90:
            return "slow"
        else:
            return "very_slow"

    # ==================== Community Engagement Analysis ====================

    async def _analyze_community_engagement(self, owner: str, repo: str) -> Dict[str, Any]:
        """
        Analyze community engagement and participation.

        Evaluates:
        - Pull request activity
        - External contributor ratio
        - Discussion/comment activity
        - Community growth trends
        """
        logger.debug(f"Analyzing community engagement for {owner}/{repo}")

        try:
            # Fetch data concurrently
            results = await asyncio.gather(
                self._fetch_pull_requests(owner, repo, state="all"),
                self._fetch_repo_stats(owner, repo),
                self._fetch_contributors(owner, repo),
                return_exceptions=True,
            )

            prs = results[0] if not isinstance(results[0], Exception) else []
            repo_stats = results[1] if not isinstance(results[1], Exception) else {}
            contributors = results[2] if not isinstance(results[2], Exception) else []

            # Analyze pull requests
            now = datetime.utcnow()
            recent_cutoff = now - timedelta(days=90)

            total_prs = len(prs)
            merged_prs = 0
            external_prs = 0
            recent_prs = 0
            pr_merge_times = []
            pr_by_month: Dict[str, int] = defaultdict(int)

            # Get repo owner for external contributor detection
            repo_owner = owner.lower()

            for pr in prs:
                # Check if merged
                if pr.get("merged_at"):
                    merged_prs += 1

                    # Calculate merge time
                    created = pr.get("created_at")
                    merged = pr.get("merged_at")
                    if created and merged:
                        try:
                            created_dt = datetime.fromisoformat(created.replace("Z", "+00:00")).replace(tzinfo=None)
                            merged_dt = datetime.fromisoformat(merged.replace("Z", "+00:00")).replace(tzinfo=None)
                            merge_days = (merged_dt - created_dt).days
                            pr_merge_times.append(merge_days)
                        except Exception:
                            pass

                # Check if external contributor
                pr_author = pr.get("user", {}).get("login", "").lower()
                if pr_author and pr_author != repo_owner:
                    external_prs += 1

                # Check if recent
                created = pr.get("created_at")
                if created:
                    try:
                        created_dt = datetime.fromisoformat(created.replace("Z", "+00:00")).replace(tzinfo=None)
                        if created_dt >= recent_cutoff:
                            recent_prs += 1

                        month_key = created_dt.strftime("%Y-%m")
                        pr_by_month[month_key] += 1
                    except Exception:
                        pass

            # Calculate metrics
            merge_rate = merged_prs / total_prs if total_prs > 0 else 0
            external_ratio = external_prs / total_prs if total_prs > 0 else 0
            avg_merge_time = sum(pr_merge_times) / len(pr_merge_times) if pr_merge_times else 0

            # Contributor analysis
            total_contributors = len(contributors)
            contributor_commits = sum(c.get("contributions", 0) for c in contributors)

            # Calculate bus factor (min contributors for 50% of work)
            bus_factor = self._calculate_bus_factor(contributors, contributor_commits)

            # Repo engagement stats
            stars = repo_stats.get("stargazers_count", 0)
            forks = repo_stats.get("forks_count", 0)
            watchers = repo_stats.get("subscribers_count", 0)

            # Calculate engagement score
            # Based on stars, forks, contributors, PR activity
            star_score = min(50, stars / 20)  # 1000 stars = 50 points
            fork_score = min(20, forks / 10)  # 200 forks = 20 points
            contributor_score = min(20, total_contributors / 5)  # 100 contributors = 20 points
            pr_activity_score = min(10, recent_prs / 5)  # 50 recent PRs = 10 points

            engagement_score = star_score + fork_score + contributor_score + pr_activity_score

            # PR health score
            pr_health = (
                merge_rate * 40 +
                min(40, (1 - avg_merge_time / 30) * 40) +  # Faster merge = better
                external_ratio * 20  # External contributions
            )
            pr_health = max(0, min(100, pr_health))

            # Combined score
            score = engagement_score * 0.5 + pr_health * 0.5
            score = max(0, min(100, score))

            recent_months = sorted(pr_by_month.items(), reverse=True)[:6]

            return {
                "score": round(score, 2),
                "repository_stats": {
                    "stars": stars,
                    "forks": forks,
                    "watchers": watchers,
                    "contributors": total_contributors,
                },
                "pull_request_activity": {
                    "total_prs": total_prs,
                    "merged_prs": merged_prs,
                    "merge_rate": round(merge_rate, 2),
                    "recent_prs_90_days": recent_prs,
                    "average_merge_time_days": round(avg_merge_time, 1),
                    "external_contributor_prs": external_prs,
                    "external_ratio": round(external_ratio, 2),
                },
                "contributor_stats": {
                    "total_contributors": total_contributors,
                    "total_contributions": contributor_commits,
                    "avg_contributions_per_contributor": round(
                        contributor_commits / total_contributors, 1
                    ) if total_contributors > 0 else 0,
                    "bus_factor": bus_factor,
                },
                "monthly_pr_activity": dict(recent_months),
                "engagement_level": self._categorize_engagement(engagement_score),
                "pr_health_score": round(pr_health, 2),
                "status": self._get_status(score),
            }

        except Exception as e:
            logger.error(f"Error analyzing community engagement: {e}")
            return self._empty_community()

    async def _fetch_pull_requests(self, owner: str, repo: str, state: str = "all") -> List[Dict]:
        """Fetch pull requests from the repository."""
        url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
        params = {"state": state, "per_page": 100, "sort": "updated", "direction": "desc"}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=self.github_headers, params=params)
                if response.status_code == 200:
                    return response.json()
        except Exception as e:
            logger.debug(f"Error fetching PRs: {e}")

        return []

    async def _fetch_repo_stats(self, owner: str, repo: str) -> Dict:
        """Fetch repository statistics."""
        url = f"https://api.github.com/repos/{owner}/{repo}"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=self.github_headers)
                if response.status_code == 200:
                    return response.json()
        except Exception as e:
            logger.debug(f"Error fetching repo stats: {e}")

        return {}

    def _calculate_bus_factor(self, contributors: List[Dict], total: int) -> int:
        """Calculate bus factor (minimum contributors needed for 50% of work)."""
        if not contributors or total == 0:
            return 0

        sorted_contribs = sorted(
            [c.get("contributions", 0) for c in contributors],
            reverse=True
        )

        cumulative = 0
        bus_factor = 0
        threshold = total * 0.5

        for contrib in sorted_contribs:
            cumulative += contrib
            bus_factor += 1
            if cumulative >= threshold:
                break

        return bus_factor

    def _categorize_engagement(self, score: float) -> str:
        """Categorize community engagement level."""
        if score >= 80:
            return "highly_engaged"
        elif score >= 60:
            return "well_engaged"
        elif score >= 40:
            return "moderately_engaged"
        elif score >= 20:
            return "low_engagement"
        else:
            return "minimal_engagement"

    # ==================== Helper Methods ====================

    def _extract_owner_repo(self, repo_url: str) -> Optional[Tuple[str, str]]:
        """Extract owner and repo from GitHub URL."""
        patterns = [
            r"github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$",
            r"github\.com:([^/]+)/([^/]+?)(?:\.git)?/?$",
        ]
        for pattern in patterns:
            match = re.search(pattern, repo_url)
            if match:
                owner = match.group(1)
                repo = match.group(2).replace(".git", "")
                return (owner, repo)
        return None

    def _calculate_overall_score(
        self,
        commits: Dict[str, Any],
        releases: Dict[str, Any],
        issues: Dict[str, Any],
        community: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Calculate overall development activity score."""
        weights = {
            "commit_activity": 0.30,
            "release_cadence": 0.20,
            "issue_resolution": 0.25,
            "community_engagement": 0.25,
        }

        scores = {
            "commit_activity": commits.get("score", 0),
            "release_cadence": releases.get("score", 0),
            "issue_resolution": issues.get("score", 0),
            "community_engagement": community.get("score", 0),
        }

        weighted_sum = sum(scores[k] * weights[k] for k in weights)

        return {
            "score": round(weighted_sum, 2),
            "max_score": 100,
            "percentage": round(weighted_sum, 2),
            "status": self._get_status(weighted_sum),
            "component_scores": scores,
            "weights": weights,
        }

    def _get_status(self, score: float) -> str:
        """Get status label based on score."""
        if score >= 80:
            return "excellent"
        elif score >= 60:
            return "good"
        elif score >= 40:
            return "moderate"
        elif score >= 20:
            return "low"
        else:
            return "minimal"

    def _get_timestamp(self) -> str:
        """Get current timestamp in ISO format."""
        return datetime.utcnow().isoformat() + "Z"

    # ==================== Empty Result Helpers ====================

    def _empty_result(self, repo_name: str) -> Dict[str, Any]:
        """Return empty result structure."""
        return {
            "package_name": repo_name,
            "repository": "unknown",
            "timestamp": self._get_timestamp(),
            "commit_activity": self._empty_commits(),
            "release_cadence": self._empty_releases(),
            "issue_resolution": self._empty_issues(),
            "community_engagement": self._empty_community(),
            "overall_score": {
                "score": 0,
                "max_score": 100,
                "percentage": 0,
                "status": "unknown",
            },
        }

    def _empty_commits(self) -> Dict[str, Any]:
        return {
            "score": 0,
            "total_commits_analyzed": 0,
            "total_contributors": 0,
            "commits_by_period": {},
            "commits_per_week": 0,
            "contributor_diversity": 0,
            "commit_message_quality": {},
            "activity_trend": "unknown",
            "status": "unknown",
        }

    def _empty_releases(self) -> Dict[str, Any]:
        return {
            "score": 0,
            "total_releases": 0,
            "total_tags": 0,
            "releases_last_year": 0,
            "average_days_between_releases": 0,
            "days_since_last_release": None,
            "semver_compliance": {},
            "release_frequency": "unknown",
            "status": "unknown",
        }

    def _empty_issues(self) -> Dict[str, Any]:
        return {
            "score": 0,
            "total_issues": 0,
            "open_issues": 0,
            "closed_issues": 0,
            "close_rate": 0,
            "resolution_time": {},
            "resolution_efficiency": "unknown",
            "status": "unknown",
        }

    def _empty_community(self) -> Dict[str, Any]:
        return {
            "score": 0,
            "repository_stats": {},
            "pull_request_activity": {},
            "contributor_stats": {},
            "engagement_level": "unknown",
            "status": "unknown",
        }
