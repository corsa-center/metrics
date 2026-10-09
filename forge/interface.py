"""The platform-neutral contract every forge (GitHub, GitLab, ...) implements.

Nothing outside this package imports a concrete forge module: callers get a
forge from `Forge.for_repo(repo_type, repo_url, credentials)`, so the
code-hosting platform a package lives on is chosen, and its forge set up,
in exactly one place.

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

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


class Forge(ABC):
    """Abstract code-hosting platform used by every collector."""

    @classmethod
    def for_repo(
        cls,
        repo_type: Optional[str],
        repo_url: str,
        credentials: Optional[Dict[str, Any]] = None,
    ) -> Optional["Forge"]:
        """The forge for a repository, or None if it isn't on a supported
        platform. None means skip the package, rather than let every
        collector query the wrong host and report a false "not found".

        repo_type comes from the package file (docs/PACKAGE_CONFIG.md):
          * "github" -- github.com only. The GitHub forge talks to
            api.github.com, so GitHub Enterprise hosts are refused rather
            than silently queried against the wrong API.
          * "gitlab" -- repo_url's own host, so gitlab.com and any
            self-hosted instance work.
          * anything else -- unsupported; logged and refused.

        Without a repo_type the platform is inferred from the hostname:
        github.com (or no URL at all) is GitHub; gitlab.com and hosts listed
        under credentials["gitlab"] are GitLab; anything else is refused.

        `credentials` is config/orchestrator.yaml's api_credentials block:
        credentials["github"]["token"], and credentials["gitlab"][<host>]
        ["token"] per GitLab host. A host without a token is queried
        unauthenticated (public projects, lower rate limit).
        """
        # Imported here: both modules import this one.
        from forge.github import GitHubForge
        from forge.gitlab import GitLabForge

        credentials = credentials or {}
        github_token = (credentials.get("github") or {}).get("token") or None
        gitlab_hosts = credentials.get("gitlab") or {}

        def gitlab(host: str) -> "Forge":
            token = (gitlab_hosts.get(host) or {}).get("token") or None
            return GitLabForge(token=token, api_base=f"https://{host}/api/v4")

        host = urlparse(repo_url).netloc if repo_url else ""
        kind = str(repo_type).strip().lower() if repo_type else ""
        if kind == "github":
            if host in ("", "github.com"):
                return GitHubForge(github_token)
            logger.warning(
                f"repo_type 'github' given for {repo_url}, but only github.com is "
                f"supported (no GitHub Enterprise API base); skipping"
            )
            return None
        if kind == "gitlab":
            if not host:
                logger.warning("repo_type 'gitlab' given without a repo_url; skipping")
                return None
            return gitlab(host)
        if kind:
            logger.warning(f"Unsupported repo_type {repo_type!r} for {repo_url}; skipping")
            return None
        if host in ("", "github.com"):
            return GitHubForge(github_token)
        if host in gitlab_hosts or host == "gitlab.com":
            return gitlab(host)
        return None

    #: Short platform identifier ("github", "gitlab") that some external
    #: services (e.g. Codecov) key their own URLs by.
    platform: str

    #: Web hostname of the platform (e.g. "github.com", "gitlab.kitware.com"),
    #: used by external services (e.g. OpenSSF Scorecard) keyed by it.
    host: str

    #: Human-readable platform name for rendered output ("GitHub", "GitLab").
    display_name: str

    #: Where this platform expects its own special files, keyed by purpose.
    #: Collectors combine these with their platform-neutral paths (repo root,
    #: docs/) via platform_paths(), so a GitHub run never probes .gitlab/ and
    #: vice versa. Keys:
    #:   codeowners                -- code-owner file locations
    #:   issue_templates           -- issue template file or directory
    #:   change_request_templates  -- pull/merge request template
    #:   dependency_automation     -- dependency-update bot config
    #:   security_scan_workflows   -- dedicated code-scanning CI files
    PLATFORM_PATHS: Dict[str, List[str]] = {}

    def platform_paths(self, kind: str) -> List[str]:
        """This platform's own paths for `kind` (see PLATFORM_PATHS)."""
        return list(self.PLATFORM_PATHS.get(kind, []))

    # ------------------------------------------------------------------ #
    # Identity / helpers
    # ------------------------------------------------------------------ #

    @abstractmethod
    def extract_ref(self, repo_url: str) -> Optional[str]:
        """Return this forge's ref for repo_url, or None if it isn't one."""

    @abstractmethod
    def pages_url(self, ref: str) -> str:
        """Predictable static-site (Pages) URL for ref."""

    @abstractmethod
    def web_url(self, ref: str, path: str, kind: str = "blob") -> str:
        """Browsable URL for a path on the default branch. kind is "blob"
        (a file) or "tree" (a directory)."""

    @abstractmethod
    def raw_url(self, ref: str, path: str) -> str:
        """URL serving a file's raw bytes from the default branch, outside
        the REST API (so it doesn't spend API rate-limit quota)."""

    async def raw_text(self, client: httpx.AsyncClient, ref: str, path: str) -> Optional[str]:
        """A file's text via raw_url(), or None if it couldn't be read.
        Best-effort: unlike file_content() it doesn't separate "absent" from
        "couldn't tell", so use it where a missing read just means less
        evidence, not a negative result."""
        url = self.raw_url(ref, path)
        try:
            resp = await client.get(url)
        except Exception as e:
            logger.debug(f"Could not read {url}: {e!r}")
            return None
        return resp.text if resp.status_code == 200 else None

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
    async def version_tags(self, client: httpx.AsyncClient, ref: str) -> List[Dict[str, Any]]:
        """Up to 50 most recent release tags (see forge.base.is_release_tag),
        newest first, as release-shaped dicts {tag_name, published_at,
        from_tag: True}. [] when they can't be listed -- callers treat tags
        as supplementary evidence alongside releases()."""

    @abstractmethod
    async def wiki_has_content(self, client: httpx.AsyncClient, ref: str) -> bool:
        """Whether the project wiki has at least one page. False when it
        can't be determined."""

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
    async def issues_opened_between(
        self, client: httpx.AsyncClient, ref: str, start: str, end: str, *, per_page: int = 100
    ) -> Optional[Dict[str, Any]]:
        """Issues created between two dates (YYYY-MM-DD, inclusive), as
        {"total_count": int, "items": [up to per_page newest, normalized
        like issues()]}, or None if it couldn't be determined."""

    @abstractmethod
    async def issues_closed_between(
        self, client: httpx.AsyncClient, ref: str, start: str, end: str
    ) -> Optional[int]:
        """How many issues were closed between two dates (YYYY-MM-DD,
        inclusive), or None if it couldn't be determined."""

    @abstractmethod
    async def labels(
        self, client: httpx.AsyncClient, ref: str, *, page: int = 1, per_page: int = 100
    ):
        """One page of the repository's issue labels as [{name}], or
        None/COLLECTION_GAP."""

    @abstractmethod
    async def recent_issues(
        self, client: httpx.AsyncClient, ref: str, since: str, *, page: int = 1, per_page: int = 100
    ):
        """Issues (never pull/merge requests) created on or after `since`
        (YYYY-MM-DD), newest first, normalized like issues() (including
        `is_outsider`), or None if the page couldn't be fetched. Unlike
        issues(), a page is full of real issues even on a repository whose
        recent activity is mostly pull requests."""

    @abstractmethod
    async def pr_reviews(
        self, client: httpx.AsyncClient, ref: str, number: int, *, per_page: int = 1
    ): ...

    @abstractmethod
    async def count_issues(
        self, client: httpx.AsyncClient, ref: str, *,
        state: Optional[str] = None,
        labels: Optional[List[str]] = None,
        issue_types: Optional[List[str]] = None,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
    ) -> Optional[int]:
        """How many issues (never pull/merge requests) match, or None if the
        count couldn't be determined -- deliberately not the same as 0.

        state: "open" or "closed" (None: both). labels / issue_types: match
        ANY of them (None: no filter). created_after / created_before:
        YYYY-MM-DD, inclusive.
        """

    # ------------------------------------------------------------------ #
    # CI/CD and deployments
    # ------------------------------------------------------------------ #

    @abstractmethod
    async def ci_config_files(self, client: httpx.AsyncClient, ref: str):
        """CI definition files, or COLLECTION_GAP.

        Each entry: {name, path, html_url, primary}. `primary` marks the
        platform's root CI file (.gitlab-ci.yml); GitHub has no single root
        file, so every workflow is primary=False. An empty list means the
        repo has no CI configuration on this platform.
        """

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
