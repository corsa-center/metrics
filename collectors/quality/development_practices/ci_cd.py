import logging
import datetime

from dateutil import parser
from typing import Any, Dict

from integrations.github_api import GitHubClient


class CICDMetricsCollector:
    """
    Collects metrics for CI/CD metrics from Gitlab and Github.
    """



    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)

        # Initialize API clients
        credentials = config.get("api_credentials", {})
        self.github = GitHubClient(credentials.get("github", {}))

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        repo_name = package.get("name", "Unknown")
        repo_url = package.get("repo_url", "")
        output = {}
        output.update(await self.workflow_execution_time(repo_url))
        output.update(await self.percentage_workflow_success(repo_url))
        output.update(await self.deployment_frequency(repo_url))
        output.update(await self.average_time_failure(repo_url))
        output.update(await self.avg_cycle_time(repo_url))
        return output
    


    def _get_last_n_workflow_runs(self, repo_url, num_workflows=100, branch="main", status=None):
        repo = self.github.get_repository(repo_url)
        if status:
            workflow_runs = repo.get_workflow_runs(branch=branch, status=status)
        else:
            workflow_runs = repo.get_workflow_runs(branch=branch)
        return workflow_runs[num_workflows*-1:]
        

    async def workflow_execution_time(self, repo_url):
        avg_workflow_execution_time = 0
        workflows = self._get_last_n_workflow_runs(repo_url=repo_url)
        for w in workflows:
            workflow_duration = parser.parse(w.updated_at).timestamp - parser.parse(w.created_at).timestamp
            avg_workflow_execution_time += workflow_duration
        return {"average_workflow_execution_time": avg_workflow_execution_time / 100}


    async def percentage_workflow_success(self, repo_url):
        workflows = self.github.get_repository(repo_url).get_workflows()
        workflow_success_percentage = {}
        for w in workflows:
            runs = w.get_runs(exclude_pull_requests=True)
            for r in runs:
                if r.completed and r.status == "success":
                    successes +=1
            workflow_success_percentage[w.id] = successes / runs.totalCount * 100
        return {"workflow_success_percentage": workflow_success_percentage}


    async def deployment_frequency(self, repo_url, days_to_measure=30):
        deployments = self.github.get_repository(repo_url=repo_url).get_deployments()
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



    async def average_time_failure(self, repo_url):
        workflows_runs = self._get_last_n_workflow_runs(repo_url, 100, "main", "failure")
        average_time_to_failure = 0
        for w in workflows_runs:
            workflow_duration = parser.parse(w.updated_at).timestamp - parser.parse(w.created_at).timestamp
            average_time_to_failure += workflow_duration
        return {"average_time_to_failure": average_time_to_failure / 100}
            


    async def avg_cycle_time(self, repo_url):
        pull_requests = self.github.get_repository(repo_url).get_pulls(state="closed")
        
        avg_cycle_time = 0
        for pull_request in pull_requests:
            first_commit = pull_request.get_commits()[0]
            statuses = first_commit.get_statuses()
            first_status_date = None
            for s in statuses:
                if not first_status_date:
                    first_status_date = s.createdAt
                else:
                    if parser.parse(first_status_date) >= parser.parse(s.createdAt):
                        first_status_date = s.createdAt
            cycle_time = parser.parse(pull_request.closedAt) - parser.parse(first_status_date)
            avg_cycle_time += cycle_time

        return {"avg_cycle_time": avg_cycle_time / len(pull_requests)}

