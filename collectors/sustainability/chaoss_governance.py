"""
CHAOSS Governance Health Indicators Collector

Collects standardized CHAOSS metrics for governance and community health:
- Issues Inclusivity: Diverse participation in issues
- Documentation Usability: README quality and documentation presence
- Time to Close: Average time to close issues
- Change Request Closure Ratio: Percentage of merged PRs
- Project Popularity: Stars, forks, watchers
- Libyears: Dependency freshness (when data available)
- Issue Age: Distribution of open issue ages
- Release Frequency: How often releases are published

CHAOSS Metrics Reference: https://chaoss.community/metrics/
"""

import asyncio
import httpx
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
import re
from statistics import mean, median

logger = logging.getLogger(__name__)


class CHAOSSGovernanceCollector:
    """
    Collects CHAOSS-defined governance health indicators

    CHAOSS Metrics Covered:
    - Issues Inclusivity (contributor diversity)
    - Documentation Usability (README, docs quality)
    - Time to Close (issue resolution time)
    - Change Request Closure Ratio (PR merge rate)
    - Project Popularity (stars, forks, watchers)
    - Issue Age (open issue distribution)
    - Release Frequency (release cadence)
    """

    def __init__(self, github_token: Optional[str] = None):
        """
        Initialize collector with optional GitHub token

        Args:
            github_token: Optional GitHub token for API access
        """
        self.github_token = github_token
        if github_token:
            self.github_headers = {
                "Authorization": f"token {github_token}",
                "Accept": "application/vnd.github.v3+json"
            }
        else:
            self.github_headers = {
                "Accept": "application/vnd.github.v3+json"
            }

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        """
        Collect CHAOSS governance health indicators for a package

        Args:
            package: Dictionary with 'name' and 'repo_url' keys

        Returns:
            Dictionary with CHAOSS governance metrics
        """
        repo_name = package.get("name", "Unknown")
        repo_url = package.get("repo_url", "")

        logger.info(f"Collecting CHAOSS metrics for {repo_name}")

        # Extract owner/repo from URL
        owner_repo = self._extract_owner_repo(repo_url)
        if not owner_repo:
            logger.error(f"Could not extract owner/repo from {repo_url}")
            return self._empty_result(repo_name)

        owner, repo = owner_repo

        # Collect all CHAOSS metrics concurrently
        logger.info("Collecting CHAOSS health indicators...")

        results = await asyncio.gather(
            self._get_project_popularity(owner, repo),
            self._get_documentation_usability(owner, repo),
            self._get_issue_metrics(owner, repo),
            self._get_change_request_metrics(owner, repo),
            self._get_release_frequency(owner, repo),
            self._get_issues_inclusivity(owner, repo),
            return_exceptions=True
        )

        # Unpack results
        popularity = results[0] if not isinstance(results[0], Exception) else {}
        documentation = results[1] if not isinstance(results[1], Exception) else {}
        issue_metrics = results[2] if not isinstance(results[2], Exception) else {}
        pr_metrics = results[3] if not isinstance(results[3], Exception) else {}
        release_freq = results[4] if not isinstance(results[4], Exception) else {}
        inclusivity = results[5] if not isinstance(results[5], Exception) else {}

        # Calculate overall score
        overall_score = self._calculate_overall_score(
            popularity,
            documentation,
            issue_metrics,
            pr_metrics,
            release_freq,
            inclusivity
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

    async def _get_project_popularity(self, owner: str, repo: str) -> Dict[str, Any]:
        """
        CHAOSS Metric: Project Popularity
        Measures community interest through stars, forks, watchers
        """
        logger.info("  Measuring project popularity...")
        url = f"https://api.github.com/repos/{owner}/{repo}"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, headers=self.github_headers)
                if response.status_code == 200:
                    data = response.json()

                    stars = data.get("stargazers_count", 0)
                    forks = data.get("forks_count", 0)
                    watchers = data.get("watchers_count", 0)
                    subscribers = data.get("subscribers_count", 0)

                    # Calculate popularity score (0-100)
                    # Normalize based on typical OSS project ranges
                    star_score = min(100, (stars / 1000) * 100) if stars else 0
                    fork_score = min(100, (forks / 200) * 100) if forks else 0
                    watch_score = min(100, (watchers / 100) * 100) if watchers else 0

                    avg_score = (star_score + fork_score + watch_score) / 3

                    logger.info(f"    ✓ Stars: {stars}, Forks: {forks}, Watchers: {watchers}")

                    return {
                        "stars": stars,
                        "forks": forks,
                        "watchers": watchers,
                        "subscribers": subscribers,
                        "score": round(avg_score, 2),
                        "created_at": data.get("created_at"),
                        "updated_at": data.get("updated_at"),
                    }
                else:
                    logger.warning(f"    ✗ Failed to get popularity metrics: {response.status_code}")
                    return {}
        except Exception as e:
            logger.error(f"    ✗ Error getting popularity: {e}")
            return {}

    async def _get_documentation_usability(self, owner: str, repo: str) -> Dict[str, Any]:
        """
        CHAOSS Metric: Documentation Usability
        Checks for README quality and documentation presence
        """
        logger.info("  Assessing documentation usability...")

        # Check for documentation files
        doc_files = {
            "readme": ["README.md", "README.txt", "README.rst", "README"],
            "contributing": ["CONTRIBUTING.md", ".github/CONTRIBUTING.md"],
            "docs_folder": ["docs/", "documentation/", "doc/"],
            "wiki": None,  # Will check via API
        }

        found_docs = []
        doc_details = {}

        # Check README
        readme_data = await self._get_readme_content(owner, repo)
        if readme_data:
            found_docs.append("readme")
            doc_details["readme"] = {
                "exists": True,
                "size": readme_data.get("size", 0),
                "quality_score": self._assess_readme_quality(readme_data.get("content", "")),
            }
            logger.info(f"    ✓ README found (quality: {doc_details['readme']['quality_score']}/100)")
        else:
            doc_details["readme"] = {"exists": False, "quality_score": 0}
            logger.info("    ✗ README not found")

        # Check for contributing guide
        for pattern in doc_files["contributing"]:
            if await self._check_file_exists(owner, repo, pattern):
                found_docs.append("contributing")
                doc_details["contributing"] = {"exists": True, "file": pattern}
                logger.info(f"    ✓ Contributing guide: {pattern}")
                break
        else:
            doc_details["contributing"] = {"exists": False}
            logger.info("    ✗ Contributing guide not found")

        # Check for docs folder
        for pattern in doc_files["docs_folder"]:
            if await self._check_file_exists(owner, repo, pattern):
                found_docs.append("docs_folder")
                doc_details["docs_folder"] = {"exists": True, "path": pattern}
                logger.info(f"    ✓ Documentation folder: {pattern}")
                break
        else:
            doc_details["docs_folder"] = {"exists": False}
            logger.info("    ✗ Documentation folder not found")

        # Check if wiki is enabled
        has_wiki = await self._check_wiki_enabled(owner, repo)
        if has_wiki:
            found_docs.append("wiki")
            doc_details["wiki"] = {"exists": True}
            logger.info("    ✓ Wiki enabled")
        else:
            doc_details["wiki"] = {"exists": False}

        # Calculate documentation score
        doc_count = len(found_docs)
        max_docs = 4
        base_score = (doc_count / max_docs) * 100

        # Add README quality bonus
        readme_quality = doc_details.get("readme", {}).get("quality_score", 0)
        doc_score = (base_score * 0.6) + (readme_quality * 0.4)

        return {
            "score": round(doc_score, 2),
            "found": found_docs,
            "details": doc_details,
            "count": doc_count,
            "max_count": max_docs,
        }

    async def _get_issue_metrics(self, owner: str, repo: str) -> Dict[str, Any]:
        """
        CHAOSS Metrics: Time to Close and Issue Age
        """
        logger.info("  Analyzing issue metrics...")

        # Get recently closed issues for time-to-close calculation
        closed_issues = await self._get_closed_issues(owner, repo, limit=30)

        # Get open issues for issue age calculation
        open_issues = await self._get_open_issues(owner, repo, limit=50)

        # Calculate time to close
        time_to_close_data = self._calculate_time_to_close(closed_issues)

        # Calculate issue age distribution
        issue_age_data = self._calculate_issue_age(open_issues)

        return {
            "time_to_close": time_to_close_data,
            "issue_age": issue_age_data,
        }

    async def _get_change_request_metrics(self, owner: str, repo: str) -> Dict[str, Any]:
        """
        CHAOSS Metric: Change Request Closure Ratio
        Percentage of PRs that get merged vs closed without merge
        """
        logger.info("  Calculating change request closure ratio...")

        # Get recent closed PRs
        closed_prs = await self._get_closed_pull_requests(owner, repo, limit=50)

        if not closed_prs:
            logger.info("    ✗ No recent PRs found")
            return {
                "closure_ratio": {
                    "total": 0,
                    "merged": 0,
                    "closed_without_merge": 0,
                    "ratio": 0,
                    "score": 0,
                }
            }

        merged = sum(1 for pr in closed_prs if pr.get("merged_at"))
        closed_without_merge = len(closed_prs) - merged
        ratio = (merged / len(closed_prs)) * 100 if closed_prs else 0

        logger.info(f"    ✓ Merged: {merged}/{len(closed_prs)} ({ratio:.1f}%)")

        return {
            "closure_ratio": {
                "total": len(closed_prs),
                "merged": merged,
                "closed_without_merge": closed_without_merge,
                "ratio": round(ratio, 2),
                "score": round(ratio, 2),  # Higher is better
            }
        }

    async def _get_release_frequency(self, owner: str, repo: str) -> Dict[str, Any]:
        """
        CHAOSS Metric: Release Frequency
        How often the project releases new versions
        """
        logger.info("  Measuring release frequency...")

        url = f"https://api.github.com/repos/{owner}/{repo}/releases"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    url,
                    headers=self.github_headers,
                    params={"per_page": 30}
                )

                if response.status_code == 200:
                    releases = response.json()

                    if not releases:
                        logger.info("    ✗ No releases found")
                        return {
                            "total_releases": 0,
                            "recent_releases": 0,
                            "avg_days_between_releases": 0,
                            "latest_release": None,
                            "score": 0,
                        }

                    # Calculate release frequency
                    recent_releases = [
                        r for r in releases
                        if self._parse_date(r.get("published_at")) > datetime.utcnow() - timedelta(days=365)
                    ]

                    # Calculate average days between releases
                    if len(releases) >= 2:
                        release_dates = [self._parse_date(r.get("published_at")) for r in releases[:10]]
                        release_dates = [d for d in release_dates if d]
                        release_dates.sort(reverse=True)

                        if len(release_dates) >= 2:
                            days_between = []
                            for i in range(len(release_dates) - 1):
                                delta = (release_dates[i] - release_dates[i + 1]).days
                                days_between.append(delta)

                            avg_days = mean(days_between) if days_between else 0
                        else:
                            avg_days = 0
                    else:
                        avg_days = 0

                    # Calculate score (more frequent = better, up to a point)
                    if avg_days == 0:
                        freq_score = 0
                    elif avg_days <= 30:
                        freq_score = 100
                    elif avg_days <= 90:
                        freq_score = 80
                    elif avg_days <= 180:
                        freq_score = 60
                    elif avg_days <= 365:
                        freq_score = 40
                    else:
                        freq_score = 20

                    latest = releases[0] if releases else None

                    logger.info(f"    ✓ {len(recent_releases)} releases in last year, avg {avg_days:.0f} days between")

                    return {
                        "total_releases": len(releases),
                        "recent_releases": len(recent_releases),
                        "avg_days_between_releases": round(avg_days, 1),
                        "latest_release": {
                            "tag": latest.get("tag_name"),
                            "published_at": latest.get("published_at"),
                        } if latest else None,
                        "score": freq_score,
                    }
                else:
                    logger.warning(f"    ✗ Failed to get releases: {response.status_code}")
                    return {}
        except Exception as e:
            logger.error(f"    ✗ Error getting releases: {e}")
            return {}

    async def _get_issues_inclusivity(self, owner: str, repo: str) -> Dict[str, Any]:
        """
        CHAOSS Metric: Issues Inclusivity
        Measures diversity of contributors in issues
        """
        logger.info("  Assessing issues inclusivity...")

        # Get recent issues with comments
        issues = await self._get_recent_issues_with_comments(owner, repo, limit=30)

        if not issues:
            logger.info("    ✗ No recent issues found")
            return {
                "total_issues": 0,
                "unique_participants": 0,
                "avg_participants_per_issue": 0,
                "score": 0,
            }

        # Count unique participants
        all_participants = set()
        participants_per_issue = []

        for issue in issues:
            issue_participants = set()
            # Add issue author
            if issue.get("user", {}).get("login"):
                issue_participants.add(issue["user"]["login"])

            # Add commenters (would need additional API call for full data)
            # For now, use comments count as proxy
            comments_count = issue.get("comments", 0)
            participants_per_issue.append(min(comments_count + 1, 10))  # Cap estimate

            if issue.get("user", {}).get("login"):
                all_participants.add(issue["user"]["login"])

        unique_count = len(all_participants)
        avg_participants = mean(participants_per_issue) if participants_per_issue else 0

        # Calculate inclusivity score
        # More unique participants = more inclusive
        inclusivity_score = min(100, (unique_count / len(issues)) * 100) if issues else 0

        logger.info(f"    ✓ {unique_count} unique participants across {len(issues)} issues")

        return {
            "total_issues": len(issues),
            "unique_participants": unique_count,
            "avg_participants_per_issue": round(avg_participants, 1),
            "score": round(inclusivity_score, 2),
        }

    # Helper methods

    async def _get_readme_content(self, owner: str, repo: str) -> Optional[Dict[str, Any]]:
        """Get README content and metadata"""
        url = f"https://api.github.com/repos/{owner}/{repo}/readme"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, headers=self.github_headers)
                if response.status_code == 200:
                    data = response.json()
                    # Decode content (it's base64 encoded)
                    import base64
                    content = base64.b64decode(data.get("content", "")).decode("utf-8", errors="ignore")
                    return {
                        "size": data.get("size", 0),
                        "content": content,
                    }
                return None
        except Exception:
            return None

    def _assess_readme_quality(self, content: str) -> float:
        """
        Assess README quality based on content
        Returns score 0-100
        """
        if not content:
            return 0

        score = 0
        max_score = 100

        # Check for key sections (each worth 20 points)
        sections = {
            "installation": r"(?i)(install|setup|getting started)",
            "usage": r"(?i)(usage|how to|example|quick start)",
            "contributing": r"(?i)(contribut|development)",
            "license": r"(?i)(license|copyright)",
            "description": r"(?i)(description|about|overview|introduction)",
        }

        for section, pattern in sections.items():
            if re.search(pattern, content):
                score += 20

        # Length bonus (well-documented projects have detailed READMEs)
        if len(content) > 1000:
            score = min(100, score + 10)

        # Has badges (indicates active maintenance)
        if re.search(r"!\[.*?\]\(.*?\)", content):
            score = min(100, score + 10)

        return min(max_score, score)

    async def _check_wiki_enabled(self, owner: str, repo: str) -> bool:
        """Check if repository wiki is enabled"""
        url = f"https://api.github.com/repos/{owner}/{repo}"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, headers=self.github_headers)
                if response.status_code == 200:
                    data = response.json()
                    return data.get("has_wiki", False)
                return False
        except Exception:
            return False

    async def _get_closed_issues(self, owner: str, repo: str, limit: int = 30) -> List[Dict[str, Any]]:
        """Get recently closed issues"""
        url = f"https://api.github.com/repos/{owner}/{repo}/issues"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    url,
                    headers=self.github_headers,
                    params={"state": "closed", "per_page": limit, "sort": "updated", "direction": "desc"}
                )
                if response.status_code == 200:
                    # Filter out PRs (they show up in issues endpoint)
                    issues = [i for i in response.json() if "pull_request" not in i]
                    return issues
                return []
        except Exception:
            return []

    async def _get_open_issues(self, owner: str, repo: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Get open issues"""
        url = f"https://api.github.com/repos/{owner}/{repo}/issues"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    url,
                    headers=self.github_headers,
                    params={"state": "open", "per_page": limit}
                )
                if response.status_code == 200:
                    issues = [i for i in response.json() if "pull_request" not in i]
                    return issues
                return []
        except Exception:
            return []

    async def _get_closed_pull_requests(self, owner: str, repo: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recently closed pull requests"""
        url = f"https://api.github.com/repos/{owner}/{repo}/pulls"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    url,
                    headers=self.github_headers,
                    params={"state": "closed", "per_page": limit, "sort": "updated", "direction": "desc"}
                )
                if response.status_code == 200:
                    return response.json()
                return []
        except Exception:
            return []

    async def _get_recent_issues_with_comments(self, owner: str, repo: str, limit: int = 30) -> List[Dict[str, Any]]:
        """Get recent issues (open and closed)"""
        url = f"https://api.github.com/repos/{owner}/{repo}/issues"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    url,
                    headers=self.github_headers,
                    params={"state": "all", "per_page": limit, "sort": "updated", "direction": "desc"}
                )
                if response.status_code == 200:
                    issues = [i for i in response.json() if "pull_request" not in i]
                    return issues
                return []
        except Exception:
            return []

    def _calculate_time_to_close(self, closed_issues: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Calculate time to close metrics"""
        if not closed_issues:
            return {
                "count": 0,
                "avg_days": 0,
                "median_days": 0,
                "min_days": 0,
                "max_days": 0,
                "score": 0,
            }

        days_to_close = []
        for issue in closed_issues:
            created = self._parse_date(issue.get("created_at"))
            closed = self._parse_date(issue.get("closed_at"))

            if created and closed:
                delta = (closed - created).days
                days_to_close.append(delta)

        if not days_to_close:
            return {"count": 0, "avg_days": 0, "median_days": 0, "score": 0}

        avg = mean(days_to_close)
        med = median(days_to_close)

        # Calculate score (faster is better)
        if avg <= 7:
            score = 100
        elif avg <= 30:
            score = 80
        elif avg <= 90:
            score = 60
        elif avg <= 180:
            score = 40
        else:
            score = 20

        logger.info(f"    ✓ Avg time to close: {avg:.1f} days (median: {med:.1f})")

        return {
            "count": len(days_to_close),
            "avg_days": round(avg, 1),
            "median_days": round(med, 1),
            "min_days": min(days_to_close),
            "max_days": max(days_to_close),
            "score": score,
        }

    def _calculate_issue_age(self, open_issues: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Calculate issue age distribution"""
        if not open_issues:
            return {
                "count": 0,
                "avg_days": 0,
                "median_days": 0,
                "max_days": 0,
                "stale_issues": 0,
                "score": 0,
            }

        ages = []
        now = datetime.utcnow()

        for issue in open_issues:
            created = self._parse_date(issue.get("created_at"))
            if created:
                age = (now - created).days
                ages.append(age)

        if not ages:
            return {"count": 0, "avg_days": 0, "score": 0}

        avg = mean(ages)
        med = median(ages)
        stale = sum(1 for age in ages if age > 180)  # Over 6 months

        # Calculate score (younger issues = better)
        if avg <= 30:
            score = 100
        elif avg <= 90:
            score = 80
        elif avg <= 180:
            score = 60
        elif avg <= 365:
            score = 40
        else:
            score = 20

        logger.info(f"    ✓ Avg issue age: {avg:.1f} days, {stale} stale issues")

        return {
            "count": len(ages),
            "avg_days": round(avg, 1),
            "median_days": round(med, 1),
            "max_days": max(ages),
            "stale_issues": stale,
            "stale_percentage": round((stale / len(ages)) * 100, 1),
            "score": score,
        }

    async def _check_file_exists(self, owner: str, repo: str, path: str) -> bool:
        """Check if a file/directory exists"""
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, headers=self.github_headers)
                return response.status_code == 200
        except Exception:
            return False

    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        """Parse ISO date string"""
        if not date_str:
            return None
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except Exception:
            return None

    def _calculate_overall_score(
        self,
        popularity: Dict[str, Any],
        documentation: Dict[str, Any],
        issue_metrics: Dict[str, Any],
        pr_metrics: Dict[str, Any],
        release_freq: Dict[str, Any],
        inclusivity: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Calculate overall CHAOSS health score"""

        # Extract scores
        pop_score = popularity.get("score", 0)
        doc_score = documentation.get("score", 0)
        time_close_score = issue_metrics.get("time_to_close", {}).get("score", 0)
        issue_age_score = issue_metrics.get("issue_age", {}).get("score", 0)
        pr_score = pr_metrics.get("closure_ratio", {}).get("score", 0)
        release_score = release_freq.get("score", 0)
        incl_score = inclusivity.get("score", 0)

        # Weighted calculation
        weighted_score = (
            pop_score * 0.15 +          # Popularity
            doc_score * 0.20 +          # Documentation (important)
            time_close_score * 0.15 +   # Issue responsiveness
            issue_age_score * 0.10 +    # Issue health
            pr_score * 0.15 +           # PR merge rate
            release_score * 0.15 +      # Release cadence
            incl_score * 0.10           # Community inclusivity
        )

        # Determine status
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

    def _extract_owner_repo(self, repo_url: str) -> Optional[tuple]:
        """Extract owner and repo from GitHub URL"""
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

    def _empty_result(self, repo_name: str) -> Dict[str, Any]:
        """Return empty result structure"""
        return {
            "package_name": repo_name,
            "repository": "unknown",
            "timestamp": self._get_timestamp(),
            "chaoss_metrics": {},
            "overall_score": {
                "score": 0,
                "max_score": 100,
                "status": "error",
            },
            "assessment_method": "error",
        }

    def _get_timestamp(self) -> str:
        """Get current timestamp in ISO format"""
        return datetime.utcnow().isoformat() + "Z"


# Example usage
async def main():
    """Example of using CHAOSSGovernanceCollector"""

    import sys
    import os
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    # Example packages to test
    test_packages = [
        {
            "name": "HDF5",
            "repo_url": "https://github.com/HDFGroup/hdf5"
        },
        {
            "name": "NumPy",
            "repo_url": "https://github.com/numpy/numpy"
        },
        {
            "name": "curl",
            "repo_url": "https://github.com/curl/curl"
        }
    ]

    # Initialize collector
    github_token = os.environ.get("GITHUB_TOKEN")
    collector = CHAOSSGovernanceCollector(github_token=github_token)

    # Collect metrics for each package
    results = []
    for package in test_packages:
        print("\n" + "="*70)
        result = await collector.collect(package)
        results.append(result)

        # Pretty print results
        print(f"Package: {result['package_name']}")
        print(f"Repository: {result['repository']}")
        print(f"\nCHAOSS Activity Metrics:")

        metrics = result['chaoss_metrics']

        if metrics.get("project_popularity"):
            pop = metrics["project_popularity"]
            print(f"  Project Popularity: {pop.get('score', 0):.1f}/100")
            print(f"    Stars: {pop.get('stars', 0)}, Forks: {pop.get('forks', 0)}, Watchers: {pop.get('watchers', 0)}")

        if metrics.get("documentation_usability"):
            doc = metrics["documentation_usability"]
            print(f"  Documentation: {doc.get('score', 0):.1f}/100")

        if metrics.get("time_to_close"):
            ttc = metrics["time_to_close"]
            print(f"  Time to Close: {ttc.get('score', 0):.1f}/100 (avg: {ttc.get('avg_days', 0):.1f} days)")

        if metrics.get("issue_age"):
            age = metrics["issue_age"]
            print(f"  Issue Age: {age.get('score', 0):.1f}/100 (avg: {age.get('avg_days', 0):.1f} days, {age.get('stale_issues', 0)} stale)")

        if metrics.get("change_request_closure_ratio"):
            cr = metrics["change_request_closure_ratio"]
            print(f"  PR Merge Rate: {cr.get('score', 0):.1f}/100 ({cr.get('ratio', 0):.1f}% merged)")

        if metrics.get("release_frequency"):
            rel = metrics["release_frequency"]
            print(f"  Release Frequency: {rel.get('score', 0):.1f}/100 ({rel.get('recent_releases', 0)} in last year)")

        if metrics.get("issues_inclusivity"):
            incl = metrics["issues_inclusivity"]
            print(f"  Issues Inclusivity: {incl.get('score', 0):.1f}/100 ({incl.get('unique_participants', 0)} participants)")

        print(f"\nOverall CHAOSS Score: {result['overall_score']['score']:.2f}/100 ({result['overall_score']['status'].upper()})")
        print(f"Reference: {result['chaoss_reference']}")
        print("="*70)

    # Save results to JSON
    import json
    with open("4.2.-chaoss_activity.json", "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n4.2.-chaoss_activity.json")


if __name__ == "__main__":
    asyncio.run(main())
