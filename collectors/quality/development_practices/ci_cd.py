import asyncio
import logging
import datetime

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
        self.github = Github(auth=Token(credentials["github"]["token"]))


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
    

    async def collect(self, package: Dict[str, Any],) -> Dict[str, Any]:
        owner, repo = self._parse_repo_url(package.get("repo_url", ""))
        repo_url = f"{owner}/{repo}"
        branch = package.get("repo_branch", "main")
        output = {}
        self.logger.info("Beginning CI/CD Metric Collection")
        output.update(await self.workflow_execution_time(repo_url, branch))
        output.update(await self.percentage_workflow_success(repo_url))
        output.update(await self.deployment_frequency(repo_url))
        output.update(await self.average_time_failure(repo_url, branch))
        output.update(await self.avg_cycle_time(repo_url))
        return output
    


    async def _get_last_n_workflow_runs(self, repo_url, num_workflows=10, branch="main", status=None):
        repo = self.github.get_repo(repo_url)
        if status:
            workflow_runs = repo.get_workflow_runs(branch=branch, status=status)
        else:
            workflow_runs = repo.get_workflow_runs(branch=branch)
        if workflow_runs.totalCount > num_workflows:
            workflow_runs = list(workflow_runs)
            return workflow_runs[(num_workflows* -1):]
        return workflow_runs
        

    async def workflow_execution_time(self, repo_url, branch):
        avg_workflow_execution_time = 0
        workflows = await self._get_last_n_workflow_runs(repo_url=repo_url, branch=branch)
        for w in workflows:
            workflow_duration = w.updated_at.timestamp() - w.created_at.timestamp()
            avg_workflow_execution_time += workflow_duration
        self.logger.debug(f"Average Workflow Execution Time: {avg_workflow_execution_time / 10}")
        return {"average_workflow_execution_time": avg_workflow_execution_time / 10}


    async def percentage_workflow_success(self, repo_url):
        workflows = self.github.get_repo(repo_url).get_workflows()
        workflow_success_percentage = {}
        successes = 0
        for w in workflows:
            runs = w.get_runs(exclude_pull_requests=True)
            if runs.totalCount > 0:
                runs = list(runs)[-10:]
                for r in runs:
                    if r.completed and r.status == "success":
                        successes +=1     
                workflow_success_percentage[w.id] = successes / len(runs) * 100
        self.logger.debug(f"Workflow Success Percentage: {workflow_success_percentage}")
        return {"workflow_success_percentage": workflow_success_percentage}


    async def deployment_frequency(self, repo_url, days_to_measure=30):
        deployments = self.github.get_repo(repo_url).get_deployments()
        start_date = datetime.datetime.now() - datetime.timedelta(days_to_measure)
        deployment_count = 0
        for d in deployments:
            if d.created_at >= start_date:
                for state in d.get_statuses():
                    if state.state == "success":
                        deployment_count +=1
                        break
            else:
                return {f"num_of_deployments_last_{days_to_measure}_days": deployment_count}
        self.logger.debug(f"Number of deployments last {days_to_measure} days: {deployment_count}")
        return {f"num_of_deployments_last_{days_to_measure}_days": deployment_count}



    async def average_time_failure(self, repo_url, branch):
        workflows_runs = await self._get_last_n_workflow_runs(repo_url, 10, branch, "failure")
        average_time_to_failure = 0
        for w in workflows_runs:
            workflow_duration = w.updated_at.timestamp() - w.created_at.timestamp()
            average_time_to_failure += workflow_duration
        self.logger.debug(f"Average Time to Failure: {average_time_to_failure / 10}")
        return {"average_time_to_failure": average_time_to_failure / 10}
            


    async def avg_cycle_time(self, repo_url):
        pull_requests = self.github.get_repo(repo_url).get_pulls(state="closed")
        pull_requests = list(pull_requests)[-10:]
        avg_cycle_time = 0
        for pull_request in pull_requests:
            first_commit = pull_request.get_commits()[0]
            statuses = first_commit.get_statuses()
            first_status_date = pull_request.created_at
            for s in statuses:
                if not first_status_date:
                    first_status_date = s.created_at
                else:
                    if first_status_date >= s.created_at:
                        first_status_date = s.created_at
            cycle_time = pull_request.closed_at.timestamp() - first_status_date.timestamp()
            avg_cycle_time += cycle_time
        if len(pull_requests) > 0:
            self.logger.debug(f"Average Cycle Time: {avg_cycle_time / len(pull_requests.totalCount)}")
            return {"avg_cycle_time": avg_cycle_time / len(pull_requests.totalCount)}
        return {"avg_cycle_time": 0}


# Example usage
async def main():
     # Setup logging
    logging.basicConfig(level=logging.DEBUG)

    # Configuration
    config = {
        "api_credentials": {
            "github": {"token": "token"},  # Replace with real token
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