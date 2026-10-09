"""GitHub implementation of forge.interface.Forge, against GitHub's REST API
(plus one GraphQL query for version tags).

Get one through `Forge.for_repo(...)` rather than constructing it directly.
The private helpers (`_github_get`, `_check_file_exists`) are the former
`GitHubCollectorBase` HTTP plumbing; the public methods are the
platform-normalized interface collectors use, and this class's docstrings
are the reference for the normalized shapes GitLabForge matches.
"""

import asyncio
import base64
import re
import httpx
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlencode

from collectors.rate_limit import search_get
from forge.base import COLLECTION_GAP, is_release_tag
from forge.interface import Forge

logger = logging.getLogger(__name__)

# GitHub's author_association values that count as "part of the project"
# rather than an outside contributor. Used to compute the normalized
# `is_outsider` flag -- GitLab has no author_association equivalent, so
# GitLabForge derives the same flag from a project members lookup instead;
# either way collectors read `is_outsider`, never the raw platform field.
_INSIDE_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}


def _is_bot_login(login: str) -> bool:
    return login.endswith("[bot]") or login.endswith("-bot")


class GitHubForge(Forge):
    """Provides shared GitHub API utilities for ecosystem/quality collectors."""

    #: Short platform identifier some external services (Codecov) key their
    #: own URLs by, independent of this forge's own API shape. Collectors
    #: that call such a service read this instead of assuming "github".
    platform = "github"

    #: The code-hosting website's hostname -- distinct from the API host
    #: (api.github.com), needed by external services (OpenSSF Scorecard)
    #: that key their URLs by it. GitLab's web and API hosts are normally
    #: the same value; GitHub's are not, hence keeping this separate from
    #: api_base rather than deriving one from the other.
    host = "github.com"

    display_name = "GitHub"

    PLATFORM_PATHS = {
        "codeowners": [".github/CODEOWNERS"],
        "issue_templates": [".github/ISSUE_TEMPLATE", ".github/ISSUE_TEMPLATE.md"],
        "change_request_templates": [
            ".github/PULL_REQUEST_TEMPLATE.md", ".github/pull_request_template.md",
        ],
        "dependency_automation": [
            ".github/dependabot.yml", ".github/dependabot.yaml", ".github/renovate.json",
        ],
        "security_scan_workflows": [
            ".github/workflows/codeql.yml",
            ".github/workflows/codeql.yaml",
            ".github/workflows/codeql-analysis.yml",
            ".github/workflows/codeql-analysis.yaml",
        ],
    }

    def __init__(self, github_token: Optional[str] = None):
        if github_token:
            self.github_headers = {
                "Authorization": f"token {github_token}",
                "Accept": "application/vnd.github.v3+json",
            }
        else:
            self.github_headers = {"Accept": "application/vnd.github.v3+json"}

    def _extract_owner_repo(self, repo_url: str) -> Optional[tuple]:
        """Extract (owner, repo) from a GitHub URL."""
        patterns = [
            r"github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$",
            r"github\.com:([^/]+)/([^/]+?)(?:\.git)?/?$",
        ]
        for pattern in patterns:
            match = re.search(pattern, repo_url)
            if match:
                return (match.group(1), match.group(2).replace(".git", ""))
        return None

    async def _check_file_exists(
        self, client: httpx.AsyncClient, owner: str, repo: str, path: str
    ):
        """Return the file's html_url if it exists, None if confirmed absent
        (a real 404), or COLLECTION_GAP if we couldn't actually tell.

        Using the GitHub Contents API without a ?ref= parameter so the
        repo's actual default branch is used (works for develop, main,
        master, or any other default). Retrying a throttled request is the
        client's job now (see RetryingTransport) -- callers just need to
        build their httpx.AsyncClient with transport=RetryingTransport().

        `path` may point at a directory (e.g. ".github/workflows"), in which
        case the Contents API returns a JSON list rather than a dict — handled
        explicitly below since a plain `.get("html_url", ...)` on a list raises
        AttributeError, which previously got swallowed and misreported as
        "not found".

        COLLECTION_GAP is falsy, same as None, so `if not result:` keeps
        working unchanged for callers that haven't opted into the
        distinction; `if result is COLLECTION_GAP:` is for ones that have.
        """
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
        try:
            response = await client.get(url, headers=self.github_headers)
        except Exception as e:
            # A network-level exception never reaches RetryingTransport's own
            # status-code-based retry/logging (it's raised from inside the
            # wrapped transport, before there's a response to inspect), so
            # this is the only place that sees it -- log it here rather than
            # let it join the same silent "None" every other gap collapses
            # into.
            logger.warning(f"COLLECTION-GAP url={url} status=exception reason={e!r}")
            return COLLECTION_GAP
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list):
                return f"https://github.com/{owner}/{repo}/tree/HEAD/{path}"
            return data.get("html_url", url)
        if response.status_code == 404:
            return None
        return COLLECTION_GAP

    async def _github_get(
        self, client: httpx.AsyncClient, url: str, params: Optional[dict] = None
    ):
        """GET a GitHub API endpoint and return the parsed JSON body, None
        for a confirmed 404, or COLLECTION_GAP if we couldn't actually tell
        (rate limit, network error, other non-2xx). Per CASS §3.5, only a
        confirmed 404 should read as "absent" -- COLLECTION_GAP is falsy,
        same as None, so `if not data:` keeps working unchanged for callers
        that haven't opted into the distinction; `if data is COLLECTION_GAP:`
        is for ones that have.
        """
        try:
            response = await client.get(url, headers=self.github_headers, params=params)
        except Exception as e:
            logger.warning(f"COLLECTION-GAP url={url} status=exception reason={e!r}")
            return COLLECTION_GAP
        if response.status_code == 200:
            return response.json()
        if response.status_code == 404:
            return None
        return COLLECTION_GAP

    def _get_timestamp(self) -> str:
        """Return current UTC timestamp in ISO format."""
        return datetime.now(timezone.utc).isoformat()

    # ---------------------------------------------------------------- #
    # Semantic interface -- see the module docstring. `ref` is always a
    # normalized "owner/repo" string; GitHub's REST paths are literally
    # `repos/{ref}/...`, so no further splitting is needed here.
    # ---------------------------------------------------------------- #

    def extract_ref(self, repo_url: str) -> Optional[str]:
        """Return "owner/repo" for a GitHub repo_url, or None if it isn't one."""
        owner_repo = self._extract_owner_repo(repo_url)
        return f"{owner_repo[0]}/{owner_repo[1]}" if owner_repo else None

    async def repo_info(self, client: httpx.AsyncClient, ref: str):
        """Normalized repository info, or None (confirmed absent) / COLLECTION_GAP.

        Canonical keys, stable across forges: stars, forks, watchers,
        open_issues, description, homepage, has_wiki, has_pages,
        has_discussions, archived, disabled, language, size_kb, created_at,
        updated_at, pushed_at, default_branch, license ({spdx_id, name,
        key} or None). `has_discussions` is GitHub-only -- GitLabForge
        always returns False for it, a real answer (GitLab has no
        Discussions feature), not a gap.
        """
        data = await self._github_get(client, f"https://api.github.com/repos/{ref}")
        if data is COLLECTION_GAP or data is None:
            return data
        license_data = data.get("license") or {}
        return {
            "stars": data.get("stargazers_count", 0),
            "forks": data.get("forks_count", 0),
            "watchers": data.get("subscribers_count", 0),
            "open_issues": data.get("open_issues_count", 0),
            "description": data.get("description"),
            "homepage": (data.get("homepage") or "").strip() or None,
            "has_wiki": bool(data.get("has_wiki")),
            "has_pages": bool(data.get("has_pages")),
            "has_discussions": bool(data.get("has_discussions")),
            "archived": bool(data.get("archived")),
            "disabled": bool(data.get("disabled")),
            "language": data.get("language"),
            "size_kb": data.get("size", 0),
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at"),
            "pushed_at": data.get("pushed_at"),
            "default_branch": data.get("default_branch") or "HEAD",
            "license": {
                "spdx_id": license_data.get("spdx_id"),
                "name": license_data.get("name"),
                "key": license_data.get("key"),
            } if license_data else None,
        }

    async def file_metadata(self, client: httpx.AsyncClient, ref: str, path: str):
        """Like file_exists, but returns {html_url, size, download_url} for a
        confirmed file instead of just its URL -- for callers (license
        detection) that need to fetch the file's own content afterward.
        None for confirmed absence (including a path that's actually a
        directory) or COLLECTION_GAP.
        """
        owner, repo = ref.split("/", 1)
        data = await self._github_get(
            client, f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
        )
        if data is COLLECTION_GAP:
            return COLLECTION_GAP
        if data is None or isinstance(data, list):
            return None
        return {
            "html_url": data.get("html_url", ""),
            "size": data.get("size", 0),
            "download_url": data.get("download_url", ""),
        }

    async def license(self, client: httpx.AsyncClient, ref: str):
        """Detected license metadata, or None (no detected license) /
        COLLECTION_GAP.

        Keys: file_path, html_url, size, download_url, spdx_id, key, name,
        text (decoded file content, already included in GitHub's response
        at no extra request -- '' if absent for some reason).
        Distinct from repo_info()'s license sub-dict, which comes from a
        cheaper call but lacks file location/size -- this one is for
        collectors that need to fetch the license file's own content.
        GitLab's license metadata has no file-location/download_url
        equivalent (it's project-level detection, not tied to a specific
        blob) -- GitLabForge will need those two keys to degrade gracefully
        rather than guessing at a URL.
        """
        data = await self._github_get(client, f"https://api.github.com/repos/{ref}/license")
        if data is COLLECTION_GAP or data is None:
            return data
        license_data = data.get("license") or {}
        text = ""
        if data.get("content"):
            text = base64.b64decode(data["content"]).decode("utf-8", "replace")
        return {
            "file_path": data.get("name", "LICENSE"),
            "html_url": data.get("html_url", ""),
            "size": data.get("size", 0),
            "download_url": data.get("download_url", ""),
            "spdx_id": license_data.get("spdx_id"),
            "key": license_data.get("key", "unknown"),
            "name": license_data.get("name", "Unknown"),
            "text": text,
        }

    async def file_content(self, client: httpx.AsyncClient, ref: str, path: str):
        """Decoded text content of an arbitrary file, or None (confirmed
        absent, including a path that's actually a directory) / COLLECTION_GAP."""
        data = await self._github_get(client, f"https://api.github.com/repos/{ref}/contents/{path}")
        if data is COLLECTION_GAP:
            return COLLECTION_GAP
        if data is None or isinstance(data, list):
            return None
        return base64.b64decode(data.get("content", "")).decode("utf-8", "replace")

    async def readme(self, client: httpx.AsyncClient, ref: str):
        """Decoded README text, or None (confirmed absent) / COLLECTION_GAP."""
        data = await self._github_get(client, f"https://api.github.com/repos/{ref}/readme")
        if data is COLLECTION_GAP or data is None:
            return data
        return base64.b64decode(data.get("content", "")).decode("utf-8", "replace")

    async def releases(
        self, client: httpx.AsyncClient, ref: str, *, per_page: int = 30, page: int = 1
    ):
        """List releases (newest first), or None/COLLECTION_GAP.

        Not reshaped like repo_info: GitHub's `tag_name`/`published_at`
        fields are already spelled the same way on GitLab's Releases API,
        so raw items pass through unchanged.
        """
        return await self._github_get(
            client, f"https://api.github.com/repos/{ref}/releases",
            params={"per_page": per_page, "page": page},
        )

    async def tags(
        self, client: httpx.AsyncClient, ref: str, *, per_page: int = 30, page: int = 1
    ):
        """List tags, or None/COLLECTION_GAP. Each item has at least `name`,
        spelled the same way on GitLab's Tags API."""
        return await self._github_get(
            client, f"https://api.github.com/repos/{ref}/tags",
            params={"per_page": per_page, "page": page},
        )

    async def commits(
        self, client: httpx.AsyncClient, ref: str, *,
        per_page: int = 100, page: int = 1,
        since: Optional[str] = None, until: Optional[str] = None,
        path: Optional[str] = None,
    ):
        """List commits (newest first), or None/COLLECTION_GAP.

        Each item: {sha, message, subject (first line), author_identity,
        date, committer_date} -- unwraps GitHub's nested
        commit.author.{name,date} / top-level author.login (when the
        committer has a GitHub account) into one identity field, matching
        GitLab's flatter commit shape (author_name/authored_date at the top
        level) so callers don't branch on platform. `date` is the author
        date; `committer_date` (can differ -- a rebased or merged commit
        keeps its original author date) is kept separately for callers
        specifically measuring when a change actually landed.
        """
        params: Dict[str, Any] = {"per_page": per_page, "page": page}
        if since:
            params["since"] = since
        if until:
            params["until"] = until
        if path:
            params["path"] = path
        data = await self._github_get(
            client, f"https://api.github.com/repos/{ref}/commits", params=params
        )
        if data is COLLECTION_GAP or data is None:
            return data
        out = []
        for c in data:
            commit = c.get("commit") or {}
            message = commit.get("message") or ""
            author = c.get("author") or {}
            commit_author = commit.get("author") or {}
            commit_committer = commit.get("committer") or {}
            out.append({
                "sha": c.get("sha"),
                "message": message,
                "subject": message.split("\n")[0],
                "author_identity": author.get("login") or commit_author.get("name"),
                "date": commit_author.get("date"),
                "committer_date": commit_committer.get("date"),
            })
        return out

    async def dir_listing(self, client: httpx.AsyncClient, ref: str, path: str = ""):
        """Entries directly in a directory, or COLLECTION_GAP.

        Each entry: {name, path, html_url, size, type ("file"/"dir")}. An
        empty list is a confirmed result (directory absent or has no
        entries) -- GitHub's Contents API 404s for a missing directory,
        same as a missing file.
        """
        data = await self._github_get(
            client, f"https://api.github.com/repos/{ref}/contents/{path}".rstrip("/")
        )
        if data is COLLECTION_GAP:
            return COLLECTION_GAP
        if not isinstance(data, list):
            return []
        return [
            {
                "name": e["name"],
                "path": e.get("path", e["name"]),
                "html_url": e.get("html_url", ""),
                "size": e.get("size", 0),
                "type": e.get("type"),
            }
            for e in data
        ]

    async def ci_config_files(self, client: httpx.AsyncClient, ref: str):
        """GitHub Actions workflow files in .github/workflows (see Forge)."""
        entries = await self.dir_listing(client, ref, ".github/workflows")
        if entries is COLLECTION_GAP:
            return COLLECTION_GAP
        return [
            {"name": e["name"], "path": e["path"], "html_url": e.get("html_url", ""),
             "primary": False}
            for e in entries
            if e.get("name", "").endswith((".yml", ".yaml"))
        ]

    async def contributors(
        self, client: httpx.AsyncClient, ref: str, *, per_page: int = 100, page: int = 1
    ):
        """List contributors, or None/COLLECTION_GAP.

        Each item: {identity, commit_count}. GitHub returns a user login per
        contributor; GitLab's equivalent aggregates by name/email with no
        user identity at all -- callers (bus-factor math) must treat
        `identity` as an opaque grouping key, not assume it's a real
        username.
        """
        data = await self._github_get(
            client, f"https://api.github.com/repos/{ref}/contributors",
            params={"per_page": per_page, "page": page},
        )
        if data is COLLECTION_GAP or data is None:
            return data
        return [
            {"identity": c.get("login"), "commit_count": c.get("contributions", 0)}
            for c in data
        ]

    async def first_commit_date(self, client: httpx.AsyncClient, ref: str):
        """ISO date of the repository's oldest commit, or None/COLLECTION_GAP.

        GitHub has no direct endpoint for this; asking for one commit per
        page makes the `rel="last"` Link header point at the final (oldest)
        page, so this costs at most two requests regardless of history size.
        GitLab's commits API can do this in one request instead
        (`order_by=default&sort=asc`, first page) -- GitLabForge won't need
        this Link-header trick at all.
        """
        url = f"https://api.github.com/repos/{ref}/commits"
        try:
            resp = await client.get(url, headers=self.github_headers, params={"per_page": 1})
        except Exception as e:
            logger.warning(f"COLLECTION-GAP url={url} status=exception reason={e!r}")
            return COLLECTION_GAP
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            return COLLECTION_GAP

        last_url = self._parse_link_header(resp.headers.get("Link"), "last")
        if not last_url:
            # Single page of history: the commit already in hand is the oldest.
            data = resp.json()
            return data[0]["commit"]["committer"]["date"] if data else None

        try:
            resp = await client.get(last_url, headers=self.github_headers)
        except Exception as e:
            logger.warning(f"COLLECTION-GAP url={last_url} status=exception reason={e!r}")
            return COLLECTION_GAP
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            return COLLECTION_GAP
        data = resp.json()
        return data[0]["commit"]["committer"]["date"] if data else None

    @staticmethod
    def _parse_link_header(link_header: Optional[str], rel: str) -> Optional[str]:
        """Parse the URL for a given `rel` out of a GitHub Link pagination header."""
        if not link_header:
            return None
        for part in link_header.split(","):
            segment = part.strip()
            if f'rel="{rel}"' in segment:
                return segment.split(";")[0].strip().strip("<>")
        return None

    async def commit_participation(self, client: httpx.AsyncClient, ref: str):
        """Weekly commit counts for the last 52 weeks: {"all": [...52 ints],
        "owner": [...52 ints]}, or {} if unavailable.

        GitHub-only aggregate (its /stats/participation endpoint) -- no
        GitLab equivalent exists; GitLabForge will need to derive an
        equivalent signal from paged commits instead of wrapping a
        matching endpoint. GitHub computes this asynchronously and
        answers 202 while it works, so one retry is allowed before
        giving up -- deliberately not COLLECTION_GAP-aware, since every
        current caller already treats "unavailable" and "empty" the same
        way (falls back to {}).
        """
        url = f"https://api.github.com/repos/{ref}/stats/participation"
        try:
            resp = await client.get(url, headers=self.github_headers)
            if resp.status_code == 202:
                await asyncio.sleep(3)
                resp = await client.get(url, headers=self.github_headers)
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.debug(f"Error fetching participation stats: {e}")
        return {}

    async def contributor_weekly_stats(self, client: httpx.AsyncClient, ref: str):
        """Per-contributor weekly commit history for the last year, or [].

        GitHub-only aggregate (/stats/contributors) -- same 202-while-computing
        retry as commit_participation, and the same "no GitLab equivalent"
        caveat. Raw GitHub shape (each item's `weeks` list of {w, a, d, c}) is
        passed through unchanged; only active_maintenance.py's abandonment
        analysis reads it today.
        """
        url = f"https://api.github.com/repos/{ref}/stats/contributors"
        try:
            resp = await client.get(url, headers=self.github_headers)
            if resp.status_code == 202:
                await asyncio.sleep(3)
                resp = await client.get(url, headers=self.github_headers)
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else []
        except Exception as e:
            logger.debug(f"Error fetching contributor stats: {e}")
        return []

    async def ci_runs(
        self, client: httpx.AsyncClient, ref: str, *,
        branch: Optional[str] = None, status: Optional[str] = None,
        per_page: int = 100, page: int = 1,
    ):
        """GitHub Actions workflow runs across the whole repo, or None/COLLECTION_GAP.

        Each item: {status, conclusion, created_at, updated_at}. GitLab's
        pipelines API covers the same "every CI run regardless of which
        workflow file" concept, but GitHub Actions' per-named-workflow
        breakdown (ci_workflows/ci_workflow_runs below) has no GitLab
        equivalent -- a GitLab pipeline isn't attributed to one of several
        named workflow files the way a run is here.
        """
        params: Dict[str, Any] = {"per_page": per_page, "page": page}
        if branch:
            params["branch"] = branch
        if status:
            params["status"] = status
        data = await self._github_get(
            client, f"https://api.github.com/repos/{ref}/actions/runs", params=params
        )
        if data is COLLECTION_GAP or data is None:
            return data
        return [
            {
                "status": r.get("status"),
                "conclusion": r.get("conclusion"),
                "created_at": r.get("created_at"),
                "updated_at": r.get("updated_at"),
            }
            for r in data.get("workflow_runs", [])
        ]

    async def ci_workflows(self, client: httpx.AsyncClient, ref: str):
        """List of {id, name, path, state, html_url} GitHub Actions workflow
        definitions, or None/COLLECTION_GAP. GitHub-Actions-specific -- see
        ci_runs' docstring. Includes workflows with no file in the tree, such
        as CodeQL "default setup" (path dynamic/github-code-scanning/...)."""
        data = await self._github_get(
            client, f"https://api.github.com/repos/{ref}/actions/workflows",
            params={"per_page": 100},
        )
        if data is COLLECTION_GAP or data is None:
            return data
        return [
            {"id": w.get("id"), "name": w.get("name"), "path": w.get("path", ""),
             "state": w.get("state"), "html_url": w.get("html_url")}
            for w in data.get("workflows", [])
        ]

    async def ci_workflow_runs(
        self, client: httpx.AsyncClient, ref: str, workflow_id: Any, *, per_page: int = 100
    ):
        """Runs for one named workflow (PRs excluded), or None/COLLECTION_GAP.
        Each item: {status, conclusion}."""
        data = await self._github_get(
            client, f"https://api.github.com/repos/{ref}/actions/workflows/{workflow_id}/runs",
            params={"per_page": per_page, "exclude_pull_requests": "true"},
        )
        if data is COLLECTION_GAP or data is None:
            return data
        return [
            {"status": r.get("status"), "conclusion": r.get("conclusion")}
            for r in data.get("workflow_runs", [])
        ]

    async def deployments(
        self, client: httpx.AsyncClient, ref: str, *, per_page: int = 100, page: int = 1
    ):
        """Deployments (newest first), or None/COLLECTION_GAP.

        Each item: {created_at, statuses_url} -- statuses_url is an opaque
        GitHub-provided follow-up URL, passed through as-is (not every
        deployment's outcome is worth resolving eagerly); fetch it via
        deployment_succeeded(). GitLab's nearest equivalent is its
        Deployments/Environments API, shaped differently -- no attempt at
        parity here yet.
        """
        data = await self._github_get(
            client, f"https://api.github.com/repos/{ref}/deployments",
            params={"per_page": per_page, "page": page},
        )
        if data is COLLECTION_GAP or data is None:
            return data
        return [{"created_at": d.get("created_at"), "statuses_url": d.get("statuses_url")} for d in data]

    async def deployment_succeeded(self, client: httpx.AsyncClient, statuses_url: str) -> bool:
        """Whether any recorded status for a deployment was 'success'.

        Best-effort: returns False on any fetch problem, matching how the
        one caller already treats an unresolved status as "not a successful
        deployment" rather than distinguishing that from a confirmed failure.
        """
        try:
            resp = await client.get(statuses_url, headers=self.github_headers)
            if resp.status_code == 200:
                return any(s.get("state") == "success" for s in resp.json())
        except Exception as e:
            logger.error(f"Error fetching deployment statuses: {e}")
        return False

    async def pr_reviews(
        self, client: httpx.AsyncClient, ref: str, number: int, *, per_page: int = 1
    ):
        """Reviews left on a pull request, or None/COLLECTION_GAP.

        Raw pass-through -- no caller reads individual review fields yet,
        only presence/count. GitLab's nearest equivalent (MR approvals) is
        a materially different review model, not a field rename; left for
        real design work when GitLab support needs it.
        """
        return await self._github_get(
            client, f"https://api.github.com/repos/{ref}/pulls/{number}/reviews",
            params={"per_page": per_page},
        )

    async def search_issues(self, client: httpx.AsyncClient, query: str, *, per_page: int = 1):
        """Total count of issues/PRs matching a GitHub search query, or None
        if the search couldn't be completed (rate limited after retries, or
        a network error) -- deliberately not the same as a confirmed 0.

        `query` is the raw GitHub search qualifier string (e.g.
        'repo:o/r is:issue label:bug created:2026-01-01..2026-02-01'),
        URL-encoded here. Uses the shared cross-collector search rate
        limiter in collectors/rate_limit.py -- GitHub's search API has a
        much tighter budget (30/min) than its core REST API, and that
        limiter is process-wide on purpose, not per-forge-instance.
        GitLab's search API has different semantics entirely and may be
        disabled instance-wide on self-hosted installs -- no attempt at
        parity here yet.
        """
        resp = await search_get(
            client, f"https://api.github.com/search/issues?q={quote(query)}&per_page={per_page}",
            self.github_headers,
        )
        if resp is None:
            return None
        return resp.json().get("total_count", 0)

    async def _search_issues_page(self, client: httpx.AsyncClient, q: str, per_page: int):
        query = urlencode({"q": q, "sort": "created", "order": "desc", "per_page": per_page})
        resp = await search_get(
            client, f"https://api.github.com/search/issues?{query}", self.github_headers
        )
        if resp is None or resp.status_code != 200:
            return None
        return resp.json()

    async def issues_opened_between(
        self, client: httpx.AsyncClient, ref: str, start: str, end: str, *, per_page: int = 100
    ) -> Optional[Dict[str, Any]]:
        """See Forge.issues_opened_between (one search)."""
        data = await self._search_issues_page(
            client, f"repo:{ref} is:issue created:{start}..{end}", per_page)
        if data is None:
            return None
        return {
            "total_count": data.get("total_count", 0),
            "items": [self._normalize_issue_like(i) for i in data.get("items", [])],
        }

    async def issues_closed_between(
        self, client: httpx.AsyncClient, ref: str, start: str, end: str
    ) -> Optional[int]:
        """See Forge.issues_closed_between (one search)."""
        data = await self._search_issues_page(
            client, f"repo:{ref} is:issue closed:{start}..{end}", 1)
        return None if data is None else data.get("total_count", 0)

    async def labels(
        self, client: httpx.AsyncClient, ref: str, *, page: int = 1, per_page: int = 100
    ):
        """See Forge.labels."""
        data = await self._github_get(
            client, f"https://api.github.com/repos/{ref}/labels",
            params={"per_page": per_page, "page": page},
        )
        if data is COLLECTION_GAP or data is None:
            return data
        return [{"name": l.get("name", "")} for l in data if isinstance(l, dict)]

    async def recent_issues(
        self, client: httpx.AsyncClient, ref: str, since: str, *, page: int = 1, per_page: int = 100
    ):
        """See Forge.recent_issues. Uses search, since the issues endpoint
        also returns pull requests and can't exclude them server-side.
        Shares the process-wide search rate limiter."""
        query = urlencode({
            "q": f"repo:{ref} is:issue created:>={since}",
            "sort": "created", "order": "desc", "per_page": per_page, "page": page,
        })
        resp = await search_get(
            client, f"https://api.github.com/search/issues?{query}", self.github_headers
        )
        if resp is None or resp.status_code != 200:
            return None
        return [self._normalize_issue_like(i) for i in resp.json().get("items", [])]

    async def user(self, client: httpx.AsyncClient, login: str):
        """GitHub user/org profile, or None (confirmed absent) / COLLECTION_GAP.

        Raw pass-through -- only `company` and `type` (User vs
        Organization) are read by any caller today. GitLab's user API has
        no `company` field and no equivalent per-account User/Organization
        distinction (GitLab expresses that at the namespace/group level
        instead) -- no attempt at parity here yet.
        """
        return await self._github_get(client, f"https://api.github.com/users/{login}")

    async def community_profile(self, client: httpx.AsyncClient, ref: str):
        """GitHub's aggregated community-health-file report, or {}/COLLECTION_GAP.

        GitHub-only -- there is no GitLab equivalent, so GitLabForge always
        returns {} (a real answer: GitLab has no such aggregate), never a
        gap. Callers that need per-document detection use dir_listing/
        file_exists instead, which both forges support.
        """
        data = await self._github_get(client, f"https://api.github.com/repos/{ref}/community/profile")
        if data is COLLECTION_GAP:
            return COLLECTION_GAP
        return data or {}

    async def repo_tree(self, client: httpx.AsyncClient, ref: str):
        """Whole file layout in one call: {"files": [{"path", "size"}, ...],
        "truncated": bool}, or None/COLLECTION_GAP.

        GitHub truncates the response for very large repositories --
        `truncated` is carried through so callers can report ratios as
        approximate rather than silently wrong.
        """
        data = await self._github_get(
            client, f"https://api.github.com/repos/{ref}/git/trees/HEAD",
            params={"recursive": 1},
        )
        if data is COLLECTION_GAP or data is None:
            return data
        files = [
            {"path": e["path"], "size": e.get("size", 0)}
            for e in data.get("tree", [])
            if e.get("type") == "blob"
        ]
        return {"files": files, "truncated": bool(data.get("truncated"))}

    async def languages(self, client: httpx.AsyncClient, ref: str):
        """Language -> relative size, or None/COLLECTION_GAP.

        GitHub reports byte counts; GitLab reports percentages. Comparable
        for ranking within one platform's response (what every current
        caller uses this for), not as an absolute value or across
        platforms.
        """
        return await self._github_get(client, f"https://api.github.com/repos/{ref}/languages")

    def raw_url(self, ref: str, path: str) -> str:
        return f"https://raw.githubusercontent.com/{ref}/HEAD/{path}"

    def web_url(self, ref: str, path: str, kind: str = "blob") -> str:
        return f"https://github.com/{ref}/{kind}/HEAD/{path}"

    async def version_tags(self, client: httpx.AsyncClient, ref: str) -> List[Dict[str, Any]]:
        """See Forge.version_tags. One GraphQL query, dated by the annotated
        tag or else its commit; needs a token, so returns [] without one."""
        if "Authorization" not in self.github_headers:
            return []
        owner, repo = ref.split("/", 1)
        query = """query($o:String!,$n:String!){repository(owner:$o,name:$n){
          refs(refPrefix:"refs/tags/",first:50,orderBy:{field:TAG_COMMIT_DATE,direction:DESC}){
            nodes{name target{__typename ... on Commit{committedDate}
              ... on Tag{tagger{date} target{... on Commit{committedDate}}}}}}}}"""
        try:
            resp = await client.post(
                "https://api.github.com/graphql", headers=self.github_headers,
                json={"query": query, "variables": {"o": owner, "n": repo}},
            )
        except Exception as e:
            logger.warning(f"version_tags query failed for {ref}: {e!r}")
            return []
        if resp.status_code != 200:
            return []
        repo_data = (resp.json().get("data") or {}).get("repository") or {}
        tags = []
        for node in (repo_data.get("refs") or {}).get("nodes") or []:
            name, target = node.get("name", ""), node.get("target") or {}
            if not is_release_tag(name):
                continue
            date = ((target.get("tagger") or {}).get("date")
                    or target.get("committedDate")
                    or (target.get("target") or {}).get("committedDate"))
            if date:
                tags.append({"tag_name": name, "published_at": date, "from_tag": True})
        return tags

    async def wiki_has_content(self, client: httpx.AsyncClient, ref: str) -> bool:
        """See Forge.wiki_has_content. GitHub's has_wiki flag is on by
        default for every repository, so it says nothing on its own; the
        wiki's git endpoint only answers 200 once a page exists. Not a REST
        API call, so it costs no rate-limit quota."""
        url = f"https://github.com/{ref}.wiki.git/info/refs?service=git-upload-pack"
        try:
            resp = await client.get(url)
            return resp.status_code == 200
        except Exception as e:
            logger.debug(f"Could not check wiki for {ref}: {e}")
            return False

    def pages_url(self, ref: str) -> str:
        """Predictable Pages URL for `ref`, regardless of whether Pages is
        actually enabled -- check `repo_info(...)["has_pages"]` first."""
        owner, repo = ref.split("/", 1)
        return f"https://{owner}.github.io/{repo}/"

    async def file_exists(self, client: httpx.AsyncClient, ref: str, path: str):
        """Same contract as `_check_file_exists`, taking a combined ref."""
        owner, repo = ref.split("/", 1)
        return await self._check_file_exists(client, owner, repo, path)

    def get_timestamp(self) -> str:
        """Public alias of `_get_timestamp` for composition-based callers."""
        return self._get_timestamp()

    def _normalize_issue_like(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Add normalized fields to a raw GitHub issue or PR object in place.

        Collectors should read `is_outsider` and the other raw fields
        (state/created_at/closed_at/merged_at/comments are already the same
        names and "open"/"closed" values GitLab uses) rather than
        `author_association`, so the same collector code works once
        GitLabForge starts returning its own normalized items here too.
        """
        item["is_outsider"] = item.get("author_association") not in _INSIDE_ASSOCIATIONS
        return item

    async def issues(
        self, client: httpx.AsyncClient, ref: str, *,
        state: str = "all", per_page: int = 100, page: int = 1,
        sort: Optional[str] = None, direction: Optional[str] = None,
    ):
        """List issues (pull requests excluded), or None/COLLECTION_GAP.

        GitHub's /issues endpoint returns pull requests too, with no way to
        exclude them server-side; filtered out here so every caller gets
        real issues only, matching what "issues" means on GitLab (which has
        a genuinely separate endpoint).
        """
        params: Dict[str, Any] = {"state": state, "per_page": per_page, "page": page}
        if sort:
            params["sort"] = sort
        if direction:
            params["direction"] = direction
        data = await self._github_get(
            client, f"https://api.github.com/repos/{ref}/issues", params=params
        )
        if data is COLLECTION_GAP or data is None:
            return data
        return [self._normalize_issue_like(i) for i in data if "pull_request" not in i]

    async def pull_requests(
        self, client: httpx.AsyncClient, ref: str, *,
        state: str = "all", per_page: int = 100, page: int = 1,
        sort: Optional[str] = None, direction: Optional[str] = None,
    ):
        """List pull requests, or None/COLLECTION_GAP."""
        params: Dict[str, Any] = {"state": state, "per_page": per_page, "page": page}
        if sort:
            params["sort"] = sort
        if direction:
            params["direction"] = direction
        data = await self._github_get(
            client, f"https://api.github.com/repos/{ref}/pulls", params=params
        )
        if data is COLLECTION_GAP or data is None:
            return data
        return [self._normalize_issue_like(pr) for pr in data]

    async def issue_comments(
        self, client: httpx.AsyncClient, ref: str, number: int, *, per_page: int = 10
    ) -> List[Dict[str, Any]]:
        """Normalized comments on an issue or PR: [{author, created_at, is_bot}, ...].

        Returns [] on any failure (including a gap) -- callers of this
        method only ever use it to find the first non-bot comment, so there
        is no meaningful distinction for them between "confirmed no
        comments" and "couldn't check"; both mean "no first-response time
        available."
        """
        data = await self._github_get(
            client,
            f"https://api.github.com/repos/{ref}/issues/{number}/comments",
            params={"per_page": per_page},
        )
        if data is COLLECTION_GAP or data is None:
            return []
        return [
            {
                "author": c.get("user", {}).get("login", ""),
                "created_at": c.get("created_at"),
                "is_bot": _is_bot_login(c.get("user", {}).get("login", "")),
            }
            for c in data
        ]
