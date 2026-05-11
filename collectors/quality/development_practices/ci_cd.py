"""
CI/CD Metric Collector for CASS Framework

Collects and aggregates basic CI/CD-related metrics.
"""

import asyncio
import httpx
import logging
from datetime import datetime, timezone
from datetime import timedelta
import re
from typing import Any, Dict



class CICDMetricsCollector:
    """
    Collects metrics for CI/CD metrics from Gitlab and Github.
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)

        # Initialize API clients
        credentials = config.get("api_credentials", {})
        self.github_token = credentials.get("github", {})["token"]
        self.github_datetime_format = "%Y-%m-%dT%H:%M:%S%z"
        self.headers = {}
        if self.github_token:
            self.headers["Authorization"] = f"token {self.github_token}"
            self.headers["Accept"] = "application/vnd.github.v3+json"


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
        repo_url = repo_url.rstrip("/").removesuffix(".git")

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
        branch = package.get("repo_branch", "main")
        
        self.logger.info("Beginning CI/CD Metric Collection")
        results = {}
        results.update(await self.workflow_execution_time(repo_url, branch))
        results.update(await self.percentage_workflow_success(repo_url))
        results.update(await self.deployment_frequency(repo_url))
        results.update(await self.release_frequency(repo_url))
        results.update(await self.average_time_failure(repo_url, branch))
        results.update(await self.average_cycle_time(repo_url))
        return self._calculate_score(results)
    
    def _calculate_score(self, results: Dict) -> Dict:
        """
        Scoring based on https://docs.gitlab.com/user/analytics/dora_metrics/ and https://docs.gitlab.com/user/analytics/value_streams_dashboard/#devsecops-metrics-comparison
        These metrics can be enabled via Github actions/the Gitlab API, but would be dependent on each individual repo to enable. 
        """
        score = 0
        max_score = 6
        details = []
        details = list(results.items())

        # less than 1 day
        if results.get("average_workflow_execution_time") and (results.get("average_workflow_execution_time") / 3600) < 1:
            score += 1

        if results.get("total_workflow_success_percentage") and results.get("total_workflow_success_percentage") > 60:
            score += 1

        num_deployments = next((k for k in results.keys() if 'num_of_deployments' in k), None)
        if num_deployments and results.get(num_deployments): 
            num_days = int(re.findall(r'\d+', num_deployments)[0])
            # one in last year
            if results.get(num_deployments) >= int((num_days / 365) * 1):
                score += 1
        else:
            # Repo might not use Github for deployments
            max_score -= 1

        num_releases = next((k for k in results.keys() if 'num_of_releases' in k), None)
        if num_releases and results.get(num_releases): 
            num_days = int(re.findall(r'\d+', num_releases)[0])
            # one in last year
            if results.get(num_releases) >= int((num_days / 365) * 1):
                score += 1
        else:
            # Repo might not use Github for releases
            max_score -= 1

        # less than 1 day
        if results.get("average_time_to_failure") and (results.get("average_workflow_execution_time") / 3600) < 1:
            score += 1

        # less than 1 week
        if results.get("average_cycle_time") and (results.get("average_cycle_time") / 3600) < 7:
            score += 1

        return {"score": score, "max_score": max_score, "percentage": round((score / max_score) * 100, 2), "details": details,}


    async def _get_last_n_workflow_runs(self, repo_url: str, num_workflows: int = 100, branch: str = "main", status: str = None) -> list:
        try:
            async with httpx.AsyncClient() as client:
                results = []
                current_count = num_workflows
                page_num = 1
                while current_count > 0:
                    # 100 is max page size for Github API results
                    params = {"branch": branch, "per_page": 100, "page": page_num}
                    if status:
                        params["status"] = status
                    response = await client.get(f"{repo_url}/actions/runs", headers=self.headers, params=params)
                    
                    response.raise_for_status()
                    if current_count < 100:
                        results += response.json()["workflow_runs"][:current_count]
                    else:
                        results += response.json()["workflow_runs"]
                    page_num += 1
                    current_count -= 100
                return results
        except Exception as e:
            self.logger.error(f"Error fetching last {num_workflows} workflows from repo: {e}")
            return []


    async def workflow_execution_time(self, repo_url: str, branch: str, num_workflows: int = 100) -> dict[str, float]:
        """
        Collect the average workflow execution time for the last 100 workflow runs.
        
        Args:
            repo_url: Repository Url
            branch: Which branch in repository to pull info from.
         Returns:
            Dict containing metric information.
        """
        avg_workflow_execution_time = 0
        workflows = await self._get_last_n_workflow_runs(repo_url=repo_url, num_workflows=num_workflows, branch=branch)
        for w in workflows:
            workflow_duration = self._parse_github_datetime_string(w["updated_at"]).timestamp() - self._parse_github_datetime_string(w["created_at"]).timestamp()
            avg_workflow_execution_time += workflow_duration
        self.logger.debug(f"Average Workflow Execution Time: {avg_workflow_execution_time / max(len(workflows), 1)}")
        return {"average_workflow_execution_time": avg_workflow_execution_time / max(len(workflows), 1)}


    async def percentage_workflow_success(self, repo_url: str) -> dict[str, float]:
        """
        Collect the percentage of workflow runs that succeed for last 30 workflows in repo.
        
        Args:
            repo_url: Repository Url
         Returns:
            Dict containing metric information.
        """
        try:
            async with httpx.AsyncClient() as client:
                workflows_response = await client.get(f"{repo_url}/actions/workflows", headers=self.headers)
                workflows_response.raise_for_status()
                workflows = workflows_response.json()["workflows"]
                workflow_success_percentage = {}
                total_successes = 0
                total_runs = 0
                for w in workflows:
                    params = {"exclude_pull_requests": "true"}
                    try:
                        runs_response = await client.get(f"{repo_url}/actions/workflows/{w["id"]}/runs", headers=self.headers, params=params)
                        runs_response.raise_for_status()
                        runs = runs_response.json()["workflow_runs"]
                        successes = 0
                        for r in runs:
                            if r["status"] == "completed" and r["conclusion"] == "success":
                                successes +=1     
                        if (len(runs) > 0):
                            workflow_success_percentage[w['name']] = successes / len(runs) * 100
                            total_successes += successes
                            total_runs += len(runs)
                    except Exception as e:
                        self.logger.error(f"Error fetching runs for workflow: {w}. Error: {e}")

                self.logger.debug(f"Workflow Success Percentage: {workflow_success_percentage}")
                return {"workflow_success_percentage": workflow_success_percentage, "total_workflow_success_percentage": total_successes / max(total_runs, 1) * 100}
        except Exception as e:
            self.logger.error(f"Error fetching workflows from repo: {e}")
            return {"workflow_success_percentage": {}}


    async def deployment_frequency(self, repo_url: str, days_to_measure: int = 365, pages: int = 1, page_size: int = 100) -> dict[str, int]:
        """
        Collect metrics on how many deployments over the specified time period from this repo.
        
        Args:
            repo_url: Repository Url
            days_to_measure: Time period in days in which to measure metric.
         Returns:
            Dict containing metric information.
        """
        try:
            async with httpx.AsyncClient() as client:
                deployments = []
                for i in range(pages):
                    response = await client.get(f"{repo_url}/deployments?per_page={page_size}&page={i}", headers=self.headers)
                    response.raise_for_status()
                    deployments += response.json()
                start_date = datetime.now(timezone.utc) - timedelta(days_to_measure)
                deployment_count = 0
                for d in deployments:
                    if self._parse_github_datetime_string(d["created_at"]) >= start_date:
                        try:
                            status_response = await client.get(d["statuses_url"], headers=self.headers)
                            status_response.raise_for_status()
                            statuses = status_response.json()
                            for state in statuses:
                                if state["state"] == "success":
                                    deployment_count +=1
                                    break
                        except Exception as e:
                            self.logger.error(f"Error fetching statuses for deployment: {d}. Error: {e}")
                    else:
                        break
                if deployment_count == 0:
                    # Check for any deployments. Repo might not deploy via Github.
                    latest_deployment_check = await client.get(f"{repo_url}/deployments", headers=self.headers)
                    if len(latest_deployment_check.json()) == 0:
                        return {f"num_of_deployments_last_{days_to_measure}_days": None}
                self.logger.debug(f"Number of deployments last {days_to_measure} days: {deployment_count}")
                return {f"num_of_deployments_last_{days_to_measure}_days": deployment_count}
        except Exception as e:
            self.logger.error(f"Error fetching deployments from repo: {e}")
            return {f"num_of_deployments_last_{days_to_measure}_days": 0}

            
    async def release_frequency(self, repo_url: str, days_to_measure: int = 365, pages: int = 1, page_size: int = 100) -> dict[str, int]:
        """
        Collect how many Github releases over the speficied time period for this repo.
        Args:
            repo_url: Repository Url
            days_to_measure: Time period in days in which to measure metric.
         Returns:
            Dict containing metric information.
        """
        try:
            async with httpx.AsyncClient() as client:
                releases = []
                for i in range(pages):
                    response = await client.get(f"{repo_url}/releases?per_page={page_size}&page={i}", headers=self.headers)
                    response.raise_for_status()
                    releases += response.json()
                start_date = datetime.now(timezone.utc) - timedelta(days_to_measure)
                release_count = 0
                for r in releases:
                    if not r.get("published_at"):
                        continue
                    if self._parse_github_datetime_string(r["published_at"]) >= start_date:
                        release_count +=1
                    else:
                        break
                if release_count == 0:
                    # Check for any releases. Repo might not release via Github.
                    latest_release_check = await client.get(f"{repo_url}/releases/latest", headers=self.headers)
                    if latest_release_check.status_code == 404:
                        return {f"num_of_releases_last_{days_to_measure}_days": None}
                self.logger.debug(f"Number of releases last {days_to_measure} days: {release_count}")
                return {f"num_of_releases_last_{days_to_measure}_days": release_count}
        except Exception as e:
            self.logger.error(f"Error fetching releases from repo: {e}")
            return {f"num_of_releases_last_{days_to_measure}_days": 0}
            

    async def average_time_failure(self, repo_url: str, branch: str, num_workflows: int = 100) -> dict[str, float]:
        """
        Collect the average time a failed workflow takes to finish (over the last 100 workflow runs).
        
        Args:
            repo_url: Repository Url
            branch: Which branch in repository to pull info from.
         Returns:
            Dict containing metric information.
        """
        workflows_runs = await self._get_last_n_workflow_runs(repo_url, num_workflows, branch, "failure")
        average_time_to_failure = 0
        for w in workflows_runs:
            workflow_duration = self._parse_github_datetime_string(w["updated_at"]).timestamp() - self._parse_github_datetime_string(w["created_at"]).timestamp()
            average_time_to_failure += workflow_duration
        self.logger.debug(f"Average Time to Failure: {average_time_to_failure / max(len(workflows_runs), 1)}")
        return {"average_time_to_failure": average_time_to_failure / max(len(workflows_runs), 1)}
            

    async def average_cycle_time(self, repo_url: str, pages: int = 1, page_size: int = 100) -> dict[str, float]:
        """
        Collect the average time it takes from a pull request to be first opened until it is merged into another branch from where it started.
        
        Args:
            repo_url: Repository Url
         Returns:
            Dict containing metric information.
        """
        try:
            async with httpx.AsyncClient() as client:
                pull_requests = []
                for i in range(pages):
                    params = {"state": "closed"}
                    response = await client.get(f"{repo_url}/pulls?per_page={page_size}&page={i}", headers=self.headers, params=params)
                    response.raise_for_status()
                    pull_requests += response.json()
                average_cycle_time = 0
                for pull_request in pull_requests:
                    if pull_request["merged_at"]:
                        cycle_time = self._parse_github_datetime_string(pull_request["merged_at"]).timestamp() - self._parse_github_datetime_string(pull_request["created_at"]).timestamp()
                        average_cycle_time += cycle_time
                if len(pull_requests) > 0:
                    self.logger.debug(f"Average Cycle Time: {average_cycle_time / max(len(pull_requests), 1)}")
                    return {"average_cycle_time": average_cycle_time / max(len(pull_requests), 1)}
                return {"average_cycle_time": 0.0}
        except Exception as e:
            self.logger.error(f"Error fetching pull requests for repo: {e}")
            return {"average_cycle_time": 0.0}


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