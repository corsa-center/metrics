"""GitHub-flavored forge: shared GitHub REST API utilities for collectors.

Two generations of methods live here during the collector migration:

- The legacy ones (`_extract_owner_repo`, `_github_get`, `_check_file_exists`)
  are collectors/ecosystem/base.py's former `GitHubCollectorBase`, moved here
  unchanged. Collectors not yet migrated still inherit `GitHubForge` and use
  these directly, hardcoding `api.github.com` URLs themselves.
- The semantic ones (`extract_ref`, `repo_info`, `file_exists`, ...) are the
  platform-normalized interface migrated collectors use instead, via
  composition (`self.forge = GitHubForge(token)`) rather than inheritance --
  so the same collector code can run against `forge/gitlab.py`'s
  `GitLabForge` unchanged once that lands. A method is added here only when
  a collector migration actually needs it, not speculatively.

Once every collector has migrated, the legacy methods and the inheritance
usage go away, leaving only the semantic interface.
"""

import base64
import re
import httpx
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from forge.base import COLLECTION_GAP

logger = logging.getLogger(__name__)

# GitHub's author_association values that count as "part of the project"
# rather than an outside contributor. Used to compute the normalized
# `is_outsider` flag -- GitLab has no author_association equivalent, so
# GitLabForge derives the same flag from a project members lookup instead;
# either way collectors read `is_outsider`, never the raw platform field.
_INSIDE_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}


def _is_bot_login(login: str) -> bool:
    return login.endswith("[bot]") or login.endswith("-bot")


class GitHubForge:
    """Provides shared GitHub API utilities for ecosystem/quality collectors."""

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
