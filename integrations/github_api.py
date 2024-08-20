"""
GitHub API Integration for CASS Metrics Collection

Handles all GitHub API interactions with rate limiting and error handling.
"""

import asyncio
import logging
from typing import Optional, Dict, Any
from github import Github, GithubException
from integrations.base import BaseAPIClient


class GitHubClient(BaseAPIClient):
    """
    GitHub API client for repository analysis

    Uses PyGithub library for API access with custom rate limiting.
    """

    def __init__(self, credentials: Dict[str, Any]):
        """
        Initialize GitHub client

        Args:
            credentials: Dict with 'token' key containing GitHub personal access token
        """
        api_key = credentials.get("token")
        super().__init__(api_key, rate_limit=5000)  # GitHub allows 5000 requests/hour

        if not api_key:
            self.logger.warning("No GitHub token provided - API will be rate limited")
            self.client = Github()  # Anonymous access (60 requests/hour)
        else:
            self.client = Github(api_key)

        # Test the connection
        try:
            user = self.client.get_user()
            self.logger.info(f"GitHub client initialized for user: {user.login}")
        except GithubException as e:
            self.logger.error(f"GitHub authentication failed: {e}")

    def _parse_repo_url(self, repo_url: str) -> tuple:
        """
        Parse GitHub repository URL to get owner and repo name

        Args:
            repo_url: Full GitHub repository URL

        Returns:
            Tuple of (owner, repo_name)

        Examples:
            'https://github.com/owner/repo' -> ('owner', 'repo')
            'https://github.com/owner/repo.git' -> ('owner', 'repo')
        """
        # Clean up URL
        repo_url = repo_url.rstrip("/").rstrip(".git")

        # Handle different URL formats
        if "github.com/" in repo_url:
            parts = repo_url.split("github.com/")[-1].split("/")
            if len(parts) >= 2:
                return parts[0], parts[1]

        raise ValueError(f"Invalid GitHub URL format: {repo_url}")

    async def get_repository(self, repo_url: str):
        """
        Get repository object from GitHub

        Args:
            repo_url: Full GitHub repository URL

        Returns:
            PyGithub Repository object
        """
        await self._check_rate_limit()

        try:
            owner, repo = self._parse_repo_url(repo_url)
            return self.client.get_repo(f"{owner}/{repo}")
        except GithubException as e:
            self.logger.error(f"Error fetching repository {repo_url}: {e}")
            raise
        except ValueError as e:
            self.logger.error(f"Invalid repository URL {repo_url}: {e}")
            raise

    async def get_file_content(self, repo_url: str, file_path: str) -> Optional[str]:
        """
        Get content of a file from repository

        Args:
            repo_url: Full GitHub repository URL
            file_path: Path to file in repository (e.g., 'CITATION.cff')

        Returns:
            File content as string, or None if file doesn't exist
        """
        try:
            repo = await self.get_repository(repo_url)

            # Try multiple possible locations/names
            possible_paths = [
                file_path,
                file_path.upper(),
                file_path.lower(),
                f".github/{file_path}",
            ]

            for path in possible_paths:
                try:
                    content = repo.get_contents(path)
                    return content.decoded_content.decode("utf-8")
                except GithubException:
                    continue

            self.logger.debug(f"File {file_path} not found in {repo_url}")
            return None

        except Exception as e:
            self.logger.warning(f"Could not fetch {file_path} from {repo_url}: {e}")
            return None

    async def get_license(self, repo_url: str) -> Optional[Dict[str, Any]]:
        """
        Get repository license information

        Args:
            repo_url: Full GitHub repository URL

        Returns:
            Dict with license info, or None if no license detected
        """
        try:
            repo = await self.get_repository(repo_url)

            license = repo.get_license()

            return {
                "name": license.license.name,
                "spdx_id": license.license.spdx_id,
                "url": license.license.url,
                "key": license.license.key,
            }

        except GithubException as e:
            if e.status == 404:
                self.logger.debug(f"No license found for {repo_url}")
                return None
            self.logger.error(f"Error fetching license for {repo_url}: {e}")
            return None

    async def get_commit_activity(
        self, repo_url: str, days: int = 90
    ) -> Dict[str, Any]:
        """
        Get recent commit activity

        Args:
            repo_url: Full GitHub repository URL
            days: Number of days to look back (default: 90)

        Returns:
            Dict with commit statistics
        """
        from datetime import datetime, timedelta

        try:
            repo = await self.get_repository(repo_url)

            since = datetime.now() - timedelta(days=days)
            commits = repo.get_commits(since=since)

            commit_count = commits.totalCount

            # Get first and last commit in range
            commit_list = list(commits[:10])  # Get first 10 to avoid API overhead

            return {
                "commit_count": commit_count,
                "days_analyzed": days,
                "commits_per_day": commit_count / days if days > 0 else 0,
                "has_recent_activity": commit_count > 0,
            }

        except Exception as e:
            self.logger.error(f"Error fetching commit activity for {repo_url}: {e}")
            return {
                "commit_count": 0,
                "days_analyzed": days,
                "commits_per_day": 0,
                "has_recent_activity": False,
            }

    async def get_repository_stats(self, repo_url: str) -> Dict[str, Any]:
        """
        Get comprehensive repository statistics

        Args:
            repo_url: Full GitHub repository URL

        Returns:
            Dict with various repository statistics
        """
        try:
            repo = await self.get_repository(repo_url)

            return {
                "stars": repo.stargazers_count,
                "forks": repo.forks_count,
                "watchers": repo.subscribers_count,
                "open_issues": repo.open_issues_count,
                "size_kb": repo.size,
                "created_at": repo.created_at.isoformat(),
                "updated_at": repo.updated_at.isoformat(),
                "pushed_at": repo.pushed_at.isoformat() if repo.pushed_at else None,
                "language": repo.language,
                "archived": repo.archived,
                "disabled": repo.disabled,
                "has_issues": repo.has_issues,
                "has_wiki": repo.has_wiki,
                "has_pages": repo.has_pages,
            }

        except Exception as e:
            self.logger.error(f"Error fetching repository stats for {repo_url}: {e}")
            return {}

    async def check_ci_cd(self, repo_url: str) -> Dict[str, Any]:
        """
        Check for CI/CD configuration

        Args:
            repo_url: Full GitHub repository URL

        Returns:
            Dict indicating presence of CI/CD configurations
        """
        ci_systems = {
            "github_actions": ".github/workflows",
            "travis": ".travis.yml",
            "circle_ci": ".circleci/config.yml",
            "gitlab_ci": ".gitlab-ci.yml",
            "jenkins": "Jenkinsfile",
        }

        detected = {}

        for system, path in ci_systems.items():
            content = await self.get_file_content(repo_url, path)
            detected[system] = content is not None

        return {
            "has_ci_cd": any(detected.values()),
            "ci_systems": [k for k, v in detected.items() if v],
        }

    async def get_contributors(self, repo_url: str, limit: int = 100) -> Dict[str, Any]:
        """
        Get contributor information

        Args:
            repo_url: Full GitHub repository URL
            limit: Maximum number of contributors to fetch

        Returns:
            Dict with contributor statistics
        """
        try:
            repo = await self.get_repository(repo_url)

            contributors = repo.get_contributors()
            contributor_list = []

            for i, contributor in enumerate(contributors):
                if i >= limit:
                    break

                contributor_list.append(
                    {
                        "login": contributor.login,
                        "contributions": contributor.contributions,
                    }
                )

            return {
                "total_contributors": contributors.totalCount,
                "top_contributors": contributor_list[:10],
                "contributor_count_fetched": len(contributor_list),
            }

        except Exception as e:
            self.logger.error(f"Error fetching contributors for {repo_url}: {e}")
            return {
                "total_contributors": 0,
                "top_contributors": [],
                "contributor_count_fetched": 0,
            }


# Example usage
async def main():
    """Example of how to use the GitHubClient"""

    # Initialize with your token
    credentials = {"token": "your_github_token_here"}  # Replace with actual token

    client = GitHubClient(credentials)

    # Test repository
    repo_url = "https://github.com/python/cpython"

    # Get various information
    print("Fetching repository stats...")
    stats = await client.get_repository_stats(repo_url)
    print(f"Stars: {stats.get('stars')}")
    print(f"Forks: {stats.get('forks')}")

    print("\nChecking for CITATION.cff...")
    citation = await client.get_file_content(repo_url, "CITATION.cff")
    print(f"Has CITATION.cff: {citation is not None}")

    print("\nChecking for CI/CD...")
    ci = await client.check_ci_cd(repo_url)
    print(f"Has CI/CD: {ci['has_ci_cd']}")
    print(f"Systems: {ci['ci_systems']}")


if __name__ == "__main__":
    asyncio.run(main())
