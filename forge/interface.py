"""The platform-neutral contract every forge (GitHub, GitLab, ...) implements.

Collectors type against `Forge` and never import a concrete forge module, so
the code-hosting platform a package lives on is chosen in exactly one place
(MetricsOrchestrator._resolve_forge) and nowhere else.

Shared conventions for every method below:

* `ref` is the forge's own normalized repository identifier, as returned by
  `extract_ref` ("owner/repo" on GitHub, the full "group/subgroup/project"
  path on GitLab). Callers treat it as opaque.
* `client` is an `httpx.AsyncClient` owned by the caller, normally built with
  `forge.base.RetryingTransport`.
* A fetch returns `None` only for a confirmed absence (a real 404) and
  `forge.base.COLLECTION_GAP` when the answer is unknown (rate limit, network
  error, capability the platform doesn't expose). See `_CollectionGap`.
* Normalized shapes (repo_info keys, commit items, issue/PR `is_outsider`,
  ...) are documented on GitHubForge, the reference implementation;
  GitLabForge maps its native responses onto the same shapes.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx


class Forge(ABC):
    """Abstract code-hosting platform used by every collector."""

    #: Short platform identifier ("github", "gitlab") that some external
    #: services (e.g. Codecov) key their own URLs by.
    platform: str

    #: Web hostname of the platform (e.g. "github.com", "gitlab.kitware.com"),
    #: used by external services (e.g. OpenSSF Scorecard) keyed by it.
    host: str

    # ------------------------------------------------------------------ #
    # Identity / helpers
    # ------------------------------------------------------------------ #

    @abstractmethod
    def extract_ref(self, repo_url: str) -> Optional[str]:
        """Return this forge's ref for repo_url, or None if it isn't one."""

    @abstractmethod
    def pages_url(self, ref: str) -> str:
        """Predictable static-site (Pages) URL for ref."""

    def get_timestamp(self) -> str:
        """Current UTC timestamp in ISO format."""
        return datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------ #
    # Repository metadata and contents
    # ------------------------------------------------------------------ #

    @abstractmethod
    async def repo_info(self, client: httpx.AsyncClient, ref: str): ...

    @abstractmethod
    async def license(self, client: httpx.AsyncClient, ref: str): ...

    @abstractmethod
    async def languages(self, client: httpx.AsyncClient, ref: str): ...

    @abstractmethod
    async def community_profile(self, client: httpx.AsyncClient, ref: str): ...

    @abstractmethod
    async def readme(self, client: httpx.AsyncClient, ref: str): ...

    @abstractmethod
    async def file_metadata(self, client: httpx.AsyncClient, ref: str, path: str): ...

    @abstractmethod
    async def file_content(self, client: httpx.AsyncClient, ref: str, path: str): ...

    @abstractmethod
    async def file_exists(self, client: httpx.AsyncClient, ref: str, path: str): ...

    @abstractmethod
    async def dir_listing(self, client: httpx.AsyncClient, ref: str, path: str = ""): ...

    @abstractmethod
    async def repo_tree(self, client: httpx.AsyncClient, ref: str): ...

    # ------------------------------------------------------------------ #
    # History, releases, contributors
    # ------------------------------------------------------------------ #

    @abstractmethod
    async def releases(
        self, client: httpx.AsyncClient, ref: str, *, per_page: int = 30, page: int = 1
    ): ...

    @abstractmethod
    async def tags(
        self, client: httpx.AsyncClient, ref: str, *, per_page: int = 30, page: int = 1
    ): ...

    @abstractmethod
    async def commits(
        self, client: httpx.AsyncClient, ref: str, *,
        per_page: int = 100, page: int = 1,
        since: Optional[str] = None, until: Optional[str] = None,
        path: Optional[str] = None,
    ): ...

    @abstractmethod
    async def first_commit_date(self, client: httpx.AsyncClient, ref: str): ...

    @abstractmethod
    async def contributors(
        self, client: httpx.AsyncClient, ref: str, *, per_page: int = 100, page: int = 1
    ): ...

    @abstractmethod
    async def commit_participation(self, client: httpx.AsyncClient, ref: str): ...

    @abstractmethod
    async def contributor_weekly_stats(self, client: httpx.AsyncClient, ref: str): ...

    @abstractmethod
    async def user(self, client: httpx.AsyncClient, login: str): ...

    # ------------------------------------------------------------------ #
    # Issues, pull/merge requests, reviews
    # ------------------------------------------------------------------ #

    @abstractmethod
    async def issues(
        self, client: httpx.AsyncClient, ref: str, *,
        state: str = "all", per_page: int = 100, page: int = 1,
        sort: Optional[str] = None, direction: Optional[str] = None,
    ): ...

    @abstractmethod
    async def pull_requests(
        self, client: httpx.AsyncClient, ref: str, *,
        state: str = "all", per_page: int = 100, page: int = 1,
        sort: Optional[str] = None, direction: Optional[str] = None,
    ): ...

    @abstractmethod
    async def issue_comments(
        self, client: httpx.AsyncClient, ref: str, number: int, *, per_page: int = 10
    ) -> List[Dict[str, Any]]: ...

    @abstractmethod
    async def pr_reviews(
        self, client: httpx.AsyncClient, ref: str, number: int, *, per_page: int = 1
    ): ...

    @abstractmethod
    async def search_issues(
        self, client: httpx.AsyncClient, query: str, *, per_page: int = 1
    ): ...

    # ------------------------------------------------------------------ #
    # CI/CD and deployments
    # ------------------------------------------------------------------ #

    @abstractmethod
    async def ci_runs(
        self, client: httpx.AsyncClient, ref: str, *,
        branch: Optional[str] = None, status: Optional[str] = None,
        per_page: int = 100, page: int = 1,
    ): ...

    @abstractmethod
    async def ci_workflows(self, client: httpx.AsyncClient, ref: str): ...

    @abstractmethod
    async def ci_workflow_runs(
        self, client: httpx.AsyncClient, ref: str, workflow_id: Any, *, per_page: int = 100
    ): ...

    @abstractmethod
    async def deployments(
        self, client: httpx.AsyncClient, ref: str, *, per_page: int = 100, page: int = 1
    ): ...

    @abstractmethod
    async def deployment_succeeded(
        self, client: httpx.AsyncClient, statuses_url: str
    ) -> bool: ...
