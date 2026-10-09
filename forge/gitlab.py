"""GitLab-flavored forge: implements the same semantic interface as
forge/github.py's GitHubForge, against GitLab's REST API v4.

Every method here exists because a migrated collector calls it through
`self.forge`; see forge/github.py's module docstring and each method's own
docstring there for what the normalized shape means and why. This module
adds GitLab-specific notes only where GitLab's data model genuinely differs
from GitHub's -- see especially:

  - `issues`/`pull_requests`: GitLab has no `author_association`, so
    `is_outsider` is computed from a cached project-members lookup instead.
  - `commit_participation`/`contributor_weekly_stats`: GitHub's two stats
    endpoints have no GitLab equivalent; both are derived here from paged
    commits, bounded by _MAX_STATS_PAGES.
  - `ci_workflows`/`ci_workflow_runs`: GitLab has no per-named-workflow-file
    breakdown the way GitHub Actions does (a GitLab pipeline isn't
    attributed to one of several workflow files) -- `ci_workflows` always
    returns [], a real answer, not a gap.
  - `deployment_succeeded`: GitLab's deployment list already includes each
    deployment's outcome inline, unlike GitHub's separate statuses_url
    follow-up -- deployments() smuggles the status string through that same
    field name so no second request is needed.
  - `search_issues`: NOT IMPLEMENTED (always returns None, i.e. "not
    collected"). GitHub's compound search-qualifier query string
    (`repo:x is:issue label:y created:date..date`) has no equivalent
    GitLab query language; GitLab's Issues API takes structured params
    (created_after/created_before/labels/state) instead. Translating the
    two properly means changing search_issues' own signature (structured
    kwargs instead of a raw query string) and updating both callers
    (reliability.py, outreach.py) -- real design work, not a mechanical
    port, deliberately left undone rather than guessed at. This means
    reliability.py's defect trend and outreach.py's newcomer-issue counts
    both read as "not collected" for GitLab repos until this lands.
  - `pages_url`: best-effort gitlab.io URL pattern, correct for gitlab.com,
    unverified for self-hosted instances (which often serve Pages from a
    separately configured wildcard domain this forge has no way to know).
"""

import asyncio
import base64
import httpx
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlparse

from forge.base import COLLECTION_GAP
from forge.interface import Forge

logger = logging.getLogger(__name__)

# Bounds worst-case pagination when deriving the two GitHub-stats-API
# equivalents from raw commits (see module docstring). 10 pages of 100 is
# the same order of magnitude as other bounded pagination in this codebase
# (e.g. active_maintenance.py's own _MAX_CONTRIBUTOR_PAGES precedent).
_MAX_STATS_PAGES = 10

# GitLab issue/MR states that read as "closed" in GitHub's two-state model
# (GitHub represents "merged" as state=closed + a populated merged_at,
# not a third state value).
_CLOSED_STATES = {"closed", "merged", "locked"}


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class GitLabForge(Forge):
    """Provides shared GitLab API v4 utilities for ecosystem/quality collectors."""

    #: Short platform identifier some external services (Codecov) key their
    #: own URLs by. Unverified against Codecov's actual v2 API convention
    #: for GitLab specifically (their docs are clearest about "github";
    #: "gitlab" is used here as the more likely match over the terser "gl"
    #: some of their older docs use) -- test_coverage.py's Codecov call for
    #: a real GitLab package should confirm this before relying on it.
    platform = "gitlab"

    def __init__(self, token: Optional[str] = None, api_base: str = "https://gitlab.com/api/v4"):
        self.api_base = api_base.rstrip("/")
        #: The code-hosting website's hostname, needed by external services
        #: (OpenSSF Scorecard) that key their URLs by it. Unlike GitHub,
        #: GitLab's web and API hosts are normally the same value, so this
        #: is derived from api_base rather than hardcoded -- correct for
        #: both gitlab.com and a self-hosted instance's own domain.
        self.host = urlparse(self.api_base).netloc
        self.headers: Dict[str, str] = {"Accept": "application/json"}
        if token:
            self.headers["PRIVATE-TOKEN"] = token
        # Per-instance, per-ref cache: project membership is fetched once
        # per collect() call (one forge instance) even though issues() and
        # pull_requests() may both need it. Not shared across collectors or
        # packages -- matches the existing per-instance-only caching this
        # codebase already accepts elsewhere (only the repo-info dedup in
        # forge/base.py is deliberately module-level/cross-instance).
        self._members_cache: Dict[str, set] = {}

    def _project_path(self, ref: str) -> str:
        """URL-encoded project ID for GitLab's API paths. `ref` may contain
        nested subgroups (e.g. "group/subgroup/project"), unlike GitHub's
        fixed two-segment owner/repo -- encoded as one path segment either
        way, per GitLab's own API convention."""
        return quote(ref, safe="")

    async def _gitlab_get(
        self, client: httpx.AsyncClient, path: str, params: Optional[dict] = None
    ):
        """GET a GitLab API path (relative to api_base) and return the
        parsed JSON body, None for a confirmed 404, or COLLECTION_GAP if we
        couldn't actually tell (rate limit, network error, other non-2xx).
        Same contract as forge/github.py's _github_get.
        """
        url = f"{self.api_base}{path}"
        try:
            response = await client.get(url, headers=self.headers, params=params)
        except Exception as e:
            logger.warning(f"COLLECTION-GAP url={url} status=exception reason={e!r}")
            return COLLECTION_GAP
        if response.status_code == 200:
            return response.json()
        if response.status_code == 404:
            return None
        return COLLECTION_GAP

    # ---------------------------------------------------------------- #
    # Semantic interface -- see forge/github.py for the contract each
    # method promises; notes here cover only GitLab-specific behavior.
    # ---------------------------------------------------------------- #

    def extract_ref(self, repo_url: str) -> Optional[str]:
        """Return the full namespace path ("group/subgroup/project") for a
        repo_url on this forge's own host, or None if it isn't one.

        Deliberately checks against self.host (the specific instance this
        forge was constructed for) rather than matching "gitlab.com"
        generically -- a self-hosted forge must not silently accept a
        gitlab.com URL, or vice versa.
        """
        if not repo_url:
            return None
        url = repo_url.rstrip("/")
        if url.endswith(".git"):
            url = url[: -len(".git")]
        for prefix in (f"https://{self.host}/", f"http://{self.host}/", f"git@{self.host}:"):
            if url.startswith(prefix):
                path = url[len(prefix):]
                return path or None
        return None

    async def repo_info(self, client: httpx.AsyncClient, ref: str):
        """Normalized repository info -- see GitHubForge.repo_info for the
        canonical key list. GitLab-specific mapping notes:

        - `watchers`: GitLab's API does not expose a subscriber/watcher
          count publicly -- always 0, a real answer, not a gap.
        - `homepage`/`language`: no equivalent field on the base project
          object -- always None.
        - `has_discussions`: GitLab has no Discussions feature -- always
          False.
        - `has_pages`: derived from pages_access_level when present.
        - `updated_at`/`pushed_at`: both map to GitLab's single
          last_activity_at -- GitLab doesn't distinguish "metadata changed"
          from "code pushed" the way GitHub does.
        - `license.spdx_id`: GitLab's license.key is often SPDX-shaped
          (e.g. "mit", "apache-2.0") but is not guaranteed to be a real
          SPDX identifier -- left as None rather than guessing, so callers
          that match against a known-SPDX-id table fall through to
          content-based detection instead of a wrong match.
        """
        data = await self._gitlab_get(
            client, f"/projects/{self._project_path(ref)}",
            params={"license": "true", "statistics": "true"},
        )
        if data is COLLECTION_GAP or data is None:
            return data
        license_data = data.get("license") or {}
        statistics = data.get("statistics") or {}
        pages_level = data.get("pages_access_level")
        return {
            "stars": data.get("star_count", 0),
            "forks": data.get("forks_count", 0),
            "watchers": 0,
            "open_issues": data.get("open_issues_count", 0),
            "description": data.get("description"),
            "homepage": None,
            "has_wiki": bool(data.get("wiki_enabled")),
            "has_pages": pages_level not in (None, "disabled"),
            "has_discussions": False,
            "archived": bool(data.get("archived")),
            "disabled": False,
            "language": None,
            "size_kb": round(statistics.get("repository_size", 0) / 1024) if statistics else 0,
            "created_at": data.get("created_at"),
            "updated_at": data.get("last_activity_at"),
            "pushed_at": data.get("last_activity_at"),
            "default_branch": data.get("default_branch") or "HEAD",
            "license": {
                "spdx_id": None,
                "name": license_data.get("name"),
                "key": license_data.get("key"),
            } if license_data else None,
        }

    async def file_metadata(self, client: httpx.AsyncClient, ref: str, path: str):
        """Like GitHubForge.file_metadata. GitLab's Files API returns
        base64 content directly (no separate download_url), so download_url
        is built from GitLab's raw-file route (/-/raw/<ref>/<path>), the
        equivalent of GitHub's raw.githubusercontent.com URL. Callers such
        as community_health fetch it directly for a content preview.
        """
        data = await self._gitlab_get(
            client,
            f"/projects/{self._project_path(ref)}/repository/files/{quote(path, safe='')}",
            params={"ref": "HEAD"},
        )
        if data is COLLECTION_GAP:
            return COLLECTION_GAP
        if data is None:
            return None
        branch = data.get('ref', 'HEAD')
        html_url = f"https://{self.host}/{ref}/-/blob/{branch}/{path}"
        return {
            "html_url": html_url,
            "size": data.get("size", 0),
            "download_url": f"https://{self.host}/{ref}/-/raw/{branch}/{path}",
        }

    async def license(self, client: httpx.AsyncClient, ref: str):
        """Detected license metadata -- see GitHubForge.license.

        GitLab's license is project-level metadata (from repo_info's
        ?license=true), not tied to a specific file the way GitHub's
        License API is -- file_path/html_url/size/download_url are all
        best-effort placeholders (the LICENSE file is assumed to exist at
        the conventional path when one is detected), not confirmed via a
        real file lookup. `text` is unavailable at this layer (GitLab's
        project license field doesn't include the file's own content) --
        callers wanting the raw text need a separate file_content() call
        against a guessed path, same as licensing.py's own manual-scan
        fallback already does for GitHub.
        """
        info = await self.repo_info(client, ref)
        if info is COLLECTION_GAP or info is None:
            return info
        license_data = info.get("license")
        if not license_data or not license_data.get("key"):
            return None
        return {
            "file_path": "LICENSE",
            "html_url": f"https://{self.host}/{ref}/-/blob/HEAD/LICENSE",
            "size": 0,
            "download_url": "",
            "spdx_id": None,
            "key": license_data.get("key", "unknown"),
            "name": license_data.get("name", "Unknown"),
            "text": "",
        }

    async def file_content(self, client: httpx.AsyncClient, ref: str, path: str):
        """Decoded text content of an arbitrary file -- see GitHubForge.file_content."""
        data = await self._gitlab_get(
            client,
            f"/projects/{self._project_path(ref)}/repository/files/{quote(path, safe='')}",
            params={"ref": "HEAD"},
        )
        if data is COLLECTION_GAP:
            return COLLECTION_GAP
        if data is None:
            return None
        content = data.get("content", "")
        encoding = data.get("encoding", "base64")
        if encoding == "base64":
            return base64.b64decode(content).decode("utf-8", "replace")
        return content

    async def readme(self, client: httpx.AsyncClient, ref: str):
        """Decoded README text -- see GitHubForge.readme.

        GitLab has no dedicated "readme" endpoint that resolves the
        filename variant the way GitHub's does -- README.md is tried
        first (the overwhelming convention), falling back to a project-info
        lookup's `readme_url` if present.
        """
        text = await self.file_content(client, ref, "README.md")
        if text is not None and text is not COLLECTION_GAP:
            return text
        if text is COLLECTION_GAP:
            return COLLECTION_GAP
        info = await self._gitlab_get(client, f"/projects/{self._project_path(ref)}")
        if info is COLLECTION_GAP:
            return COLLECTION_GAP
        readme_url = (info or {}).get("readme_url")
        if not readme_url:
            return None
        filename = readme_url.rsplit("/", 1)[-1]
        if filename == "README.md":
            return None  # already confirmed absent above
        return await self.file_content(client, ref, filename)

    async def releases(
        self, client: httpx.AsyncClient, ref: str, *, per_page: int = 30, page: int = 1
    ):
        """List releases -- see GitHubForge.releases.

        GitLab's Releases API already spells `tag_name`/`released_at` (not
        `published_at`) -- `published_at` is added as an alias here so
        every caller that reads GitHub's field name keeps working
        unchanged, same normalize-once principle as everywhere else.

        `assets` is reshaped too: GitLab returns a dict
        ({count, sources, links}) where GitHub returns a list of uploaded
        files. Only `links` (attached release files) correspond to GitHub's
        assets -- `sources` are the auto-generated source archives, which
        GitHub's assets list also omits -- so each link becomes a
        {name, browser_download_url} item.
        """
        data = await self._gitlab_get(
            client, f"/projects/{self._project_path(ref)}/releases",
            params={"per_page": per_page, "page": page},
        )
        if data is COLLECTION_GAP or data is None:
            return data
        for r in data:
            r.setdefault("published_at", r.get("released_at"))
            r["assets"] = self._normalize_release_assets(r.get("assets"))
        return data

    @staticmethod
    def _normalize_release_assets(assets: Any) -> List[Dict[str, Any]]:
        """Map GitLab's release `assets` dict onto GitHub's list shape."""
        if isinstance(assets, list):
            return assets
        if not isinstance(assets, dict):
            return []
        return [
            {
                "name": link.get("name"),
                "browser_download_url": link.get("direct_asset_url") or link.get("url", ""),
            }
            for link in assets.get("links") or []
            if isinstance(link, dict)
        ]

    async def tags(
        self, client: httpx.AsyncClient, ref: str, *, per_page: int = 30, page: int = 1
    ):
        """List tags -- see GitHubForge.tags. GitLab's Tags API already
        spells `name` the same way."""
        return await self._gitlab_get(
            client, f"/projects/{self._project_path(ref)}/repository/tags",
            params={"per_page": per_page, "page": page},
        )

    async def commits(
        self, client: httpx.AsyncClient, ref: str, *,
        per_page: int = 100, page: int = 1,
        since: Optional[str] = None, until: Optional[str] = None,
        path: Optional[str] = None,
    ):
        """List commits -- see GitHubForge.commits for the normalized shape.

        GitLab's commit object is already flat (author_name/author_email/
        authored_date/committer_name/committed_date at the top level, no
        nesting) -- `author_identity` uses author_name since GitLab commits
        carry no GitHub-account-style login the way a GitHub commit's
        top-level `author.login` sometimes does.
        """
        params: Dict[str, Any] = {"per_page": per_page, "page": page}
        if since:
            params["since"] = since
        if until:
            params["until"] = until
        if path:
            params["path"] = path
        data = await self._gitlab_get(
            client, f"/projects/{self._project_path(ref)}/repository/commits", params=params
        )
        if data is COLLECTION_GAP or data is None:
            return data
        out = []
        for c in data:
            message = c.get("message") or c.get("title") or ""
            out.append({
                "sha": c.get("id"),
                "message": message,
                "subject": c.get("title") or message.split("\n")[0],
                "author_identity": c.get("author_name"),
                "date": c.get("authored_date"),
                "committer_date": c.get("committed_date"),
            })
        return out

    async def _paged_commits(
        self, client: httpx.AsyncClient, ref: str, *, since: str, max_pages: int = _MAX_STATS_PAGES
    ):
        """Internal helper for commit_participation/contributor_weekly_stats:
        pages through commits(since=...) up to max_pages, returning
        whatever was fetched (possibly incomplete) rather than propagating
        a gap -- both callers already treat "unavailable" and "empty" the
        same way, matching GitHubForge's stats-endpoint fallback behavior.
        """
        results: List[Dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            batch = await self.commits(client, ref, since=since, per_page=100, page=page)
            if not batch or batch is COLLECTION_GAP:
                break
            results.extend(batch)
            if len(batch) < 100:
                break
        return results

    async def dir_listing(self, client: httpx.AsyncClient, ref: str, path: str = ""):
        """Entries directly in a directory -- see GitHubForge.dir_listing.

        GitLab's repository tree API paginates rather than returning
        everything for a directory in one call the way GitHub's Contents
        API does; one page of 100 is used since every current caller
        (community_health.py's doc-directory listings, reliability.py's
        flag-file directories) only scans conventional, small directories.
        `html_url` is constructed rather than returned by the API.
        """
        data = await self._gitlab_get(
            client, f"/projects/{self._project_path(ref)}/repository/tree",
            params={"path": path, "per_page": 100},
        )
        if data is COLLECTION_GAP:
            return COLLECTION_GAP
        if not isinstance(data, list):
            return []
        return [
            {
                "name": e["name"],
                "path": e.get("path", e["name"]),
                "html_url": f"https://{self.host}/{ref}/-/blob/HEAD/{e.get('path', e['name'])}",
                "size": 0,  # GitLab's tree API doesn't include blob size
                "type": "file" if e.get("type") == "blob" else "dir",
            }
            for e in data
        ]

    async def contributors(
        self, client: httpx.AsyncClient, ref: str, *, per_page: int = 100, page: int = 1
    ):
        """List contributors -- see GitHubForge.contributors.

        GitLab aggregates by commit author name/email, with no user
        identity at all (unlike GitHub, which at least sometimes resolves
        a real account) -- `identity` here is a free-text name, an even
        weaker grouping key than GitHub's already-opaque one. Callers must
        not assume it's a real, stable account identifier on either forge.
        """
        data = await self._gitlab_get(
            client, f"/projects/{self._project_path(ref)}/repository/contributors",
            params={"per_page": per_page, "page": page},
        )
        if data is COLLECTION_GAP or data is None:
            return data
        return [
            {"identity": c.get("name"), "commit_count": c.get("commits", 0)}
            for c in data
        ]

    async def first_commit_date(self, client: httpx.AsyncClient, ref: str):
        """ISO date of the repository's oldest commit -- see
        GitHubForge.first_commit_date.

        One request: GitLab's commits API can return oldest-first directly
        (order_by=default sorts by commit graph topology, ascending), unlike
        GitHub's REST API which has no such ordering and needs the
        Link-header "last page" trick.
        """
        data = await self._gitlab_get(
            client, f"/projects/{self._project_path(ref)}/repository/commits",
            params={"per_page": 1, "order_by": "default", "sort": "asc"},
        )
        if data is COLLECTION_GAP:
            return COLLECTION_GAP
        if not data:
            return None
        return data[0].get("committed_date")

    async def commit_participation(self, client: httpx.AsyncClient, ref: str):
        """Weekly commit counts for the last 52 weeks -- see
        GitHubForge.commit_participation.

        Derived from paged commits (see module docstring); "owner" (the
        repo-owner-authored subset GitHub's endpoint separates out) has no
        clean GitLab signal and is always empty -- every current caller
        (active_maintenance.py) only reads "all".
        """
        now = datetime.now(timezone.utc)
        since = (now - timedelta(weeks=52)).isoformat()
        commits = await self._paged_commits(client, ref, since=since)
        if not commits:
            return {}
        buckets = [0] * 52
        for c in commits:
            date = _parse_iso(c.get("committer_date") or c.get("date"))
            if not date:
                continue
            weeks_ago = int((now - date).days / 7)
            if 0 <= weeks_ago < 52:
                buckets[51 - weeks_ago] += 1
        return {"all": buckets, "owner": []}

    async def contributor_weekly_stats(self, client: httpx.AsyncClient, ref: str):
        """Per-contributor weekly commit history for the last two years --
        see GitHubForge.contributor_weekly_stats.

        Derived from paged commits (see module docstring), grouped by
        author_identity (a free-text name -- see contributors()'s caveat)
        into 104 weekly buckets, matching the shape
        active_maintenance.py's abandonment analysis reads
        ({"weeks": [{"c": count}, ...]} per contributor).
        """
        now = datetime.now(timezone.utc)
        since = (now - timedelta(weeks=104)).isoformat()
        commits = await self._paged_commits(client, ref, since=since)
        if not commits:
            return []
        by_author: Dict[str, List[int]] = {}
        for c in commits:
            identity = c.get("author_identity")
            date = _parse_iso(c.get("committer_date") or c.get("date"))
            if not identity or not date:
                continue
            weeks_ago = int((now - date).days / 7)
            if 0 <= weeks_ago < 104:
                by_author.setdefault(identity, [0] * 104)[103 - weeks_ago] += 1
        return [{"author": {"login": ident}, "weeks": [{"c": n} for n in weeks]}
                for ident, weeks in by_author.items()]

    async def ci_runs(
        self, client: httpx.AsyncClient, ref: str, *,
        branch: Optional[str] = None, status: Optional[str] = None,
        per_page: int = 100, page: int = 1,
    ):
        """CI runs (GitLab pipelines) -- see GitHubForge.ci_runs.

        GitHub Actions' status vocabulary ("failure") is translated to
        GitLab's ("failed") for the `status` filter; GitLab's own status
        already doubles as GitHub's status+conclusion combined (a
        terminal "success"/"failed" rather than "completed" + a separate
        conclusion), so both output keys are set to the same value.
        """
        params: Dict[str, Any] = {"per_page": per_page, "page": page}
        if branch:
            params["ref"] = branch
        if status:
            params["status"] = {"failure": "failed"}.get(status, status)
        data = await self._gitlab_get(
            client, f"/projects/{self._project_path(ref)}/pipelines", params=params
        )
        if data is COLLECTION_GAP or data is None:
            return data
        return [
            {
                "status": p.get("status"),
                "conclusion": p.get("status"),
                "created_at": p.get("created_at"),
                "updated_at": p.get("updated_at"),
            }
            for p in data
        ]

    async def ci_workflows(self, client: httpx.AsyncClient, ref: str):
        """No GitLab equivalent -- see module docstring. Always [], a real
        answer (not a gap): GitLab pipelines aren't attributed to named
        workflow files the way GitHub Actions runs are."""
        return []

    async def ci_workflow_runs(
        self, client: httpx.AsyncClient, ref: str, workflow_id: Any, *, per_page: int = 100
    ):
        """No GitLab equivalent -- see ci_workflows(). Never actually
        called in practice since ci_workflows() always returns []."""
        return []

    async def deployments(
        self, client: httpx.AsyncClient, ref: str, *, per_page: int = 100, page: int = 1
    ):
        """Deployments -- see GitHubForge.deployments.

        GitLab's deployment list already includes each deployment's own
        outcome inline (unlike GitHub, which needs a separate statuses_url
        follow-up) -- that status string is smuggled through the
        statuses_url field so deployment_succeeded() below can answer
        without a second request.
        """
        data = await self._gitlab_get(
            client, f"/projects/{self._project_path(ref)}/deployments",
            params={"per_page": per_page, "page": page, "order_by": "created_at", "sort": "desc"},
        )
        if data is COLLECTION_GAP or data is None:
            return data
        return [{"created_at": d.get("created_at"), "statuses_url": d.get("status")} for d in data]

    async def deployment_succeeded(self, client: httpx.AsyncClient, statuses_url: str) -> bool:
        """Whether a deployment succeeded -- see GitHubForge.deployment_succeeded.

        `statuses_url` here is actually the GitLab deployment's own status
        string (see deployments() above), not a URL -- no HTTP request is
        made at all.
        """
        return statuses_url == "success"

    async def pr_reviews(
        self, client: httpx.AsyncClient, ref: str, number: int, *, per_page: int = 1
    ):
        """Reviews on a merge request -- see GitHubForge.pr_reviews.

        GitLab's review model is approvals, not a list of discrete review
        events -- the approvals endpoint returns one object per MR, not a
        list. Normalized to a list of its approved_by entries so
        `bool(reviews)` (the only thing any current caller checks) still
        means the same thing: "has at least one approval."
        """
        data = await self._gitlab_get(
            client,
            f"/projects/{self._project_path(ref)}/merge_requests/{number}/approvals",
        )
        if data is COLLECTION_GAP:
            return COLLECTION_GAP
        if data is None:
            return None
        return data.get("approved_by", []) or []

    async def search_issues(self, client: httpx.AsyncClient, query: str, *, per_page: int = 1):
        """Not implemented -- see module docstring. Always returns None
        (a gap, not a confirmed 0), so callers correctly render
        "not collected" rather than a fabricated zero."""
        return None

    async def user(self, client: httpx.AsyncClient, login: str):
        """GitLab user profile -- see GitHubForge.user.

        `company`: GitLab's user object has no equivalent field -- always
        absent from the returned dict, so callers' `.get("company")` reads
        None, a real "unknown" rather than a fabricated employer.
        `type`: GitLab has no per-account User/Organization distinction
        (that's expressed at the namespace/group level instead) -- always
        absent, same reasoning.

        GitLab's Users API is list-based (`/users?username=X`), unlike
        GitHub's single-resource `/users/{login}` -- the first (and only
        expected) match is returned to preserve the single-object contract.
        """
        data = await self._gitlab_get(client, "/users", params={"username": login})
        if data is COLLECTION_GAP:
            return COLLECTION_GAP
        if not data:
            return None
        return data[0]

    async def community_profile(self, client: httpx.AsyncClient, ref: str):
        """No GitLab equivalent -- see GitHubForge.community_profile.
        Always {}, a real answer, not a gap."""
        return {}

    async def repo_tree(self, client: httpx.AsyncClient, ref: str):
        """Whole file layout -- see GitHubForge.repo_tree.

        GitLab's tree API paginates rather than returning everything (with
        a truncation flag) in one call -- paged up to _MAX_STATS_PAGES
        pages of 100 here; `truncated` is set when that cap is hit, same
        meaning as GitHub's own truncation flag (ratios computed from the
        result should be treated as approximate).
        """
        files: List[Dict[str, Any]] = []
        truncated = False
        for page in range(1, _MAX_STATS_PAGES + 1):
            data = await self._gitlab_get(
                client, f"/projects/{self._project_path(ref)}/repository/tree",
                params={"recursive": "true", "per_page": 100, "page": page},
            )
            if data is COLLECTION_GAP:
                return COLLECTION_GAP if not files else {"files": files, "truncated": True}
            if not data:
                break
            files.extend(
                {"path": e["path"], "size": 0}  # GitLab's tree API doesn't include blob size
                for e in data if e.get("type") == "blob"
            )
            if len(data) < 100:
                break
        else:
            truncated = True
        return {"files": files, "truncated": truncated}

    async def languages(self, client: httpx.AsyncClient, ref: str):
        """Language -> relative size -- see GitHubForge.languages.
        GitLab's /languages endpoint already returns this shape directly
        (percentages, not byte counts -- see GitHubForge's caveat)."""
        return await self._gitlab_get(client, f"/projects/{self._project_path(ref)}/languages")

    def pages_url(self, ref: str) -> str:
        """Best-effort Pages URL -- see module docstring's caveat about
        self-hosted instances. GitLab's documented pattern for a
        top-level-group project is https://{group}.gitlab.io/{project}/;
        for a nested subgroup, the subgroup path is appended."""
        parts = ref.split("/")
        group, rest = parts[0], parts[1:]
        rest_path = "/".join(rest)
        return f"https://{group}.gitlab.io/{rest_path}/" if rest_path else f"https://{group}.gitlab.io/"

    async def file_exists(self, client: httpx.AsyncClient, ref: str, path: str):
        """Same contract as GitHubForge.file_exists."""
        data = await self.file_metadata(client, ref, path)
        if data is COLLECTION_GAP:
            return COLLECTION_GAP
        if data is None:
            return None
        return data["html_url"]

    def get_timestamp(self) -> str:
        """Return current UTC timestamp in ISO format."""
        return datetime.now(timezone.utc).isoformat()

    async def _project_members(self, client: httpx.AsyncClient, ref: str) -> set:
        """Usernames with membership on this project (including inherited
        group membership), cached per forge instance. Used to compute
        is_outsider for issues()/pull_requests() -- GitLab has no
        author_association field, so this is the closest available signal
        for "part of the project" vs. an outside contributor. A gap here
        is treated as "assume outsider" (the safer default -- undercounting
        insiders is less misleading than overcounting them) rather than
        propagated, since no caller today distinguishes a gapped
        is_outsider from a confirmed one.
        """
        if ref in self._members_cache:
            return self._members_cache[ref]
        data = await self._gitlab_get(
            client, f"/projects/{self._project_path(ref)}/members/all",
            params={"per_page": 100},
        )
        members = {m["username"] for m in data} if isinstance(data, list) else set()
        self._members_cache[ref] = members
        return members

    def _normalize_issue_like(self, item: Dict[str, Any], members: set) -> Dict[str, Any]:
        """Reshape a raw GitLab issue or merge request into the same keys
        GitHubForge's items carry: number (from iid), state ("open"/
        "closed", collapsing GitLab's "merged"/"locked" into "closed" the
        way GitHub represents a merged PR as closed+merged_at), comments
        (from user_notes_count), user.login (from author.username),
        is_outsider.
        """
        author = item.get("author") or {}
        username = author.get("username")
        raw_state = item.get("state")
        return {
            **item,
            "number": item.get("iid"),
            "state": "closed" if raw_state in _CLOSED_STATES else "open",
            "comments": item.get("user_notes_count", 0),
            "user": {"login": username},
            "is_outsider": username not in members if username else True,
        }

    async def issues(
        self, client: httpx.AsyncClient, ref: str, *,
        state: str = "all", per_page: int = 100, page: int = 1,
        sort: Optional[str] = None, direction: Optional[str] = None,
    ):
        """List issues -- see GitHubForge.issues."""
        params: Dict[str, Any] = {
            "state": {"open": "opened"}.get(state, state),
            "per_page": per_page, "page": page,
        }
        if sort:
            params["order_by"] = {"updated": "updated_at", "created": "created_at"}.get(sort, sort)
        if direction:
            params["sort"] = direction
        data = await self._gitlab_get(
            client, f"/projects/{self._project_path(ref)}/issues", params=params
        )
        if data is COLLECTION_GAP or data is None:
            return data
        members = await self._project_members(client, ref)
        return [self._normalize_issue_like(i, members) for i in data]

    async def pull_requests(
        self, client: httpx.AsyncClient, ref: str, *,
        state: str = "all", per_page: int = 100, page: int = 1,
        sort: Optional[str] = None, direction: Optional[str] = None,
    ):
        """List merge requests -- see GitHubForge.pull_requests."""
        params: Dict[str, Any] = {
            "state": {"open": "opened"}.get(state, state),
            "per_page": per_page, "page": page,
        }
        if sort:
            params["order_by"] = {"updated": "updated_at", "created": "created_at"}.get(sort, sort)
        if direction:
            params["sort"] = direction
        data = await self._gitlab_get(
            client, f"/projects/{self._project_path(ref)}/merge_requests", params=params
        )
        if data is COLLECTION_GAP or data is None:
            return data
        members = await self._project_members(client, ref)
        return [self._normalize_issue_like(pr, members) for pr in data]

    async def issue_comments(
        self, client: httpx.AsyncClient, ref: str, number: int, *, per_page: int = 10
    ) -> List[Dict[str, Any]]:
        """Normalized comments on an issue -- see GitHubForge.issue_comments.

        GitLab calls these "notes"; system-generated notes (label changes,
        status transitions) are excluded so only real human/bot comments
        are considered, matching what GitHub's comments endpoint already
        returns natively (it has no system-note concept mixed in).
        """
        data = await self._gitlab_get(
            client, f"/projects/{self._project_path(ref)}/issues/{number}/notes",
            params={"per_page": per_page, "sort": "asc", "order_by": "created_at"},
        )
        if data is COLLECTION_GAP or data is None:
            return []
        out = []
        for c in data:
            if c.get("system"):
                continue
            author = c.get("author") or {}
            username = author.get("username", "")
            out.append({
                "author": username,
                "created_at": c.get("created_at"),
                "is_bot": bool(author.get("bot")) or username.endswith("-bot"),
            })
        return out
