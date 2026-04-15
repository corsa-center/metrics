"""
CI/CD Metric Collector for CASS Framework

Collects and aggregates basic CI/CD-related metrics.
"""

import asyncio
import httpx
import logging
from datetime import datetime
from datetime import timedelta

from dateutil import parser
from typing import Any, Dict

from github import Github
from github.Auth import Token


class CICDMetricsCollector:
    """
    Collects metrics for CI/CD metrics from Gitlab and Github.
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)

        # Initialize API clients
        credentials = config.get("api_credentials", {})
        self.github_token = credentials["github"]["token"]
        self.github_datetime_format = "%Y-%m-%dT%H:%M:%SZ"
        self.headers = {}
        if self.github_token:
            self.headers["Authorization"] = f"token {self.github_token}"
            self.headers["Accept"] = "application/vnd.github.v3+json"

        self.github = Github(auth=Token(credentials["github"]["token"]))


    def _parse_repo_url(self, repo_url: str) -> tuple:
        """
        Parse GitHub repository URL to get api base url.

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
                return  f"https://api.github.com/repos/{parts[0]}/{parts[1]}"

        raise ValueError(f"Invalid GitHub URL format: {repo_url}")
    
    def _parse_github_datetime_string(self, date):
        return datetime.strptime(date, self.github_datetime_format)

    async def collect(self, package: Dict[str, Any],) -> Dict[str, Any]:
        """
        Collect metrics.
        
        Args:
            package: Repostiory Information
         Returns:
            Dict containing metric information.
        """
        repo_url = self._parse_repo_url(package.get("repo_url", ""))
        print(repo_url)
        branch = package.get("repo_branch", "main")
        output = {}
        self.logger.info("Beginning CI/CD Metric Collection")
        output.update(await self.workflow_execution_time(repo_url, branch))
        output.update(await self.percentage_workflow_success(repo_url))
        output.update(await self.deployment_frequency(repo_url))
        output.update(await self.release_frequency(repo_url))
        output.update(await self.average_time_failure(repo_url, branch))
        output.update(await self.avg_cycle_time(repo_url))
        return output
    

    async def _get_last_n_workflow_runs(self, repo_url: str, num_workflows: int = 100, branch: str = "main", status: str = None) -> list:
        async with httpx.AsyncClient() as client:
            params = {"status": status, "branch": branch, "per_page": num_workflows}
            response = await client.get(f"{repo_url}/actions/runs", headers=self.headers, params=params)
            return response.json()["workflow_runs"]
        

    async def workflow_execution_time(self, repo_url: str, branch: str) -> dict[str, float]:
        """
        Collect the average workflow execution time for the last 100 workflow runs.
        
        Args:
            repo_url: Repository Url
            branch: Which branch in repository to pull info from.
         Returns:
            Dict containing metric information.
        """
        avg_workflow_execution_time = 0
        workflows = await self._get_last_n_workflow_runs(repo_url=repo_url, num_workflows=30, branch=branch)
        for w in workflows:
            workflow_duration = self._parse_github_datetime_string(w["updated_at"]).timestamp() - self._parse_github_datetime_string(w["created_at"]).timestamp()
            avg_workflow_execution_time += workflow_duration
        self.logger.debug(f"Average Workflow Execution Time: {avg_workflow_execution_time / 100}")
        return {"average_workflow_execution_time": avg_workflow_execution_time / 100}


    async def percentage_workflow_success(self, repo_url: str) -> dict[str, float]:
        """
        Collect the percentage of workflow runs that succeed for last 30 workflows in repo.
        
        Args:
            repo_url: Repository Url
         Returns:
            Dict containing metric information.
        """
        async with httpx.AsyncClient() as client:
            workflows_response = await client.get(f"{repo_url}/actions/workflows", headers=self.headers)
            workflows = workflows_response.json()["workflows"]
            workflow_success_percentage = {}
            for w in workflows:
                params = {"exclude_pull_requests": "true"}
                runs_response = await client.get(f"{repo_url}/actions/workflows/{w["id"]}/runs", headers=self.headers, params=params)
                runs = runs_response.json()["workflow_runs"]
                successes = 0
                for r in runs:
                    if r["status"] == "completed" and r["conclusion"] == "success":
                        successes +=1     
                if (len(runs) > 0):
                    workflow_success_percentage[w["id"]] = successes / len(runs) * 100
            self.logger.debug(f"Workflow Success Percentage: {workflow_success_percentage}")
            return {"workflow_success_percentage": workflow_success_percentage}


    async def deployment_frequency(self, repo_url: str, days_to_measure: int = 30) -> dict[str, int]:
        """
        Collect metrics on how many deployments over the specified time period from this repo.
        
        Args:
            repo_url: Repository Url
            days_to_measure: Time period in days in which to measure metric.
         Returns:
            Dict containing metric information.
        """
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{repo_url}/deployments", headers=self.headers)
            deployments = response.json()
            start_date = datetime.now() - timedelta(days_to_measure)
            deployment_count = 0
            for d in deployments:
                if self._parse_github_datetime_string(d["created_at"]) >= start_date:
                    status_response = await client.get(d["statuses_url"], headers=self.headers)
                    statuses = status_response.json()
                    for state in statuses:
                        if state["state"] == "success":
                            deployment_count +=1
                            break
                else:
                    return {f"num_of_deployments_last_{days_to_measure}_days": deployment_count}
            self.logger.debug(f"Number of deployments last {days_to_measure} days: {deployment_count}")
            return {f"num_of_deployments_last_{days_to_measure}_days": deployment_count}
        

    async def release_frequency(self, repo_url: str, days_to_measure: int = 365) -> dict[str, int]:
        """
        Collect how many Github releases over the speficied time period for this repo.
        Args:
            repo_url: Repository Url
            days_to_measure: Time period in days in which to measure metric.
         Returns:
            Dict containing metric information.
        """
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{repo_url}/releases", headers=self.headers)
            releases = response.json()
            start_date = datetime.now() - timedelta(days_to_measure)
            release_count = 0
            for r in releases:
                if self._parse_github_datetime_string(r["published_at"]) >= start_date:
                    release_count +=1
                else:
                    self.logger.debug(f"Number of releases last {days_to_measure} days: {release_count}")
                    return {f"num_of_releases_last_{days_to_measure}_days": release_count}
            self.logger.debug(f"Number of releases last {days_to_measure} days: {release_count}")
            return {f"num_of_releases_last_{days_to_measure}_days": release_count}
        


    async def average_time_failure(self, repo_url: str, branch: str) -> dict[str, float]:
        """
        Collect the average time a failed workflow takes to finish (over the last 100 workflow runs).
        
        Args:
            repo_url: Repository Url
            branch: Which branch in repository to pull info from.
         Returns:
            Dict containing metric information.
        """
        workflows_runs = await self._get_last_n_workflow_runs(repo_url, 30, branch, "failure")
        average_time_to_failure = 0
        for w in workflows_runs:
            workflow_duration = self._parse_github_datetime_string(w["updated_at"]).timestamp() - self._parse_github_datetime_string(w["created_at"]).timestamp()
            average_time_to_failure += workflow_duration
        self.logger.debug(f"Average Time to Failure: {average_time_to_failure / 100}")
        return {"average_time_to_failure": average_time_to_failure / 100}
            

    async def avg_cycle_time(self, repo_url: str) -> dict[str, float]:
        """
        Collect the average time it takes from a pull request to be first opened until it is merged into another branch from where it started.
        
        Args:
            repo_url: Repository Url
         Returns:
            Dict containing metric information.
        """
        async with httpx.AsyncClient() as client:
            params = {"state": "closed"}
            response = await client.get(f"{repo_url}/pulls", headers=self.headers, params=params)
            pull_requests = response.json()
            avg_cycle_time = 0
            for pull_request in pull_requests:
                if pull_request["merged_at"]:
                    cycle_time = self._parse_github_datetime_string(pull_request["merged_at"]).timestamp() - self._parse_github_datetime_string(pull_request["created_at"]).timestamp()
                    avg_cycle_time += cycle_time
            if len(pull_requests) > 0:
                self.logger.debug(f"Average Cycle Time: {avg_cycle_time / len(pull_requests)}")
                return {"avg_cycle_time": avg_cycle_time / len(pull_requests)}
            return {"avg_cycle_time": 0.0}


# Example usage
async def main():
     # Setup logging
    logging.basicConfig(level=logging.DEBUG)

    # Configuration
    config = {
        "api_credentials": {
            "github": {"token": ""},  # Replace with real token
            "semantic_scholar": {},
            "openalex": {},
            "zenodo": {},
        }
    }
    # Example package
    package = {
        "repo_url": "https://github.com/galaxyproject/galaxy",
        "repo_branch": "dev"
        
    }
     # Collect metrics
    collector = CICDMetricsCollector(config)
    results = await collector.collect(package)

    # Display results
    import json

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())