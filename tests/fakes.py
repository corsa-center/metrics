"""A configurable in-memory Forge for collector unit tests.

Collectors only talk to the platform through forge.interface.Forge, so a
test sets up the repository it wants -- files, README, tree, releases,
issues, search totals -- on a FakeForge and hands it to the collector. No
HTTP mocking needed.

Anything not configured behaves like an empty repository: files are absent
(None), lists are empty. To make a call fail as a collection gap, put the
method name in `gaps`, or a path in `gap_paths` for the file-level calls.
"""

from typing import Any, Callable, Dict, List, Optional, Union

from forge.base import COLLECTION_GAP
from forge.interface import Forge


class FakeForge(Forge):
    platform = "github"
    host = "github.com"
    display_name = "GitHub"

    def __init__(self, **kwargs: Any):
        self.ref = "o/r"
        # path -> text. A directory exists if any file lives under it.
        self.files: Dict[str, str] = {}
        self.dirs: List[str] = []
        self.readme_text: Optional[str] = None
        self.repo_info_data: Optional[Dict[str, Any]] = {}
        self.license_data: Optional[Dict[str, Any]] = None
        self.tree_truncated = False
        self.release_list: List[Dict[str, Any]] = []
        self.tag_list: List[Dict[str, Any]] = []
        self.version_tag_list: List[Dict[str, Any]] = []
        self.commit_list: List[Dict[str, Any]] = []
        self.contributor_list: List[Dict[str, Any]] = []
        self.issue_list: List[Dict[str, Any]] = []
        self.pr_list: List[Dict[str, Any]] = []
        self.comments: Dict[int, List[Dict[str, Any]]] = {}
        self.label_list: List[Dict[str, Any]] = []
        self.users: Dict[str, Dict[str, Any]] = {}
        self.workflows: List[Dict[str, Any]] = []
        self.ci_files: Optional[List[Dict[str, Any]]] = None
        self.ci_run_list: List[Dict[str, Any]] = []
        self.deployment_list: List[Dict[str, Any]] = []
        self.wiki = False
        self.participation: Any = {}
        self.weekly_stats: List[Dict[str, Any]] = []
        self.first_commit: Optional[str] = None
        self.languages_data: Dict[str, int] = {}
        self.community: Dict[str, Any] = {}
        # Issue search: query -> total, or a callable(query) -> total/None.
        self.search: Union[Dict[str, Optional[int]], Callable[[str], Optional[int]]] = {}
        self.opened_between: Optional[Dict[str, Any]] = {"total_count": 0, "items": []}
        self.closed_between: Optional[int] = 0
        self.recent_issue_pages: List[Optional[List[Dict[str, Any]]]] = []
        self.gaps: set = set()
        self.gap_paths: set = set()
        self.calls: List[tuple] = []
        for key, value in kwargs.items():
            setattr(self, key, value)

    # ------------------------------------------------------------ helpers

    def _gap(self, method: str) -> bool:
        self.calls.append((method,))
        return method in self.gaps

    def _has_dir(self, path: str) -> bool:
        path = path.rstrip("/")
        return path in self.dirs or any(p.startswith(path + "/") for p in self.files)

    # ------------------------------------------------------------ identity

    def extract_ref(self, repo_url: str) -> Optional[str]:
        if not repo_url or "://" not in repo_url and "@" not in repo_url:
            return None
        return self.ref

    def pages_url(self, ref: str) -> str:
        owner, repo = ref.split("/", 1)
        return f"https://{owner}.github.io/{repo}/"

    def web_url(self, ref: str, path: str, kind: str = "blob") -> str:
        return f"https://github.com/{ref}/{kind}/HEAD/{path}"

    def raw_url(self, ref: str, path: str) -> str:
        return f"https://raw.example/{ref}/{path}"

    async def raw_text(self, client, ref, path):
        self.calls.append(("raw_text", path))
        if path in self.gap_paths:
            return None
        return self.files.get(path)

    def get_timestamp(self) -> str:
        return "2026-01-01T00:00:00+00:00"

    # ------------------------------------------------------------ contents

    async def repo_info(self, client, ref):
        return COLLECTION_GAP if self._gap("repo_info") else self.repo_info_data

    async def license(self, client, ref):
        return COLLECTION_GAP if self._gap("license") else self.license_data

    async def languages(self, client, ref):
        return COLLECTION_GAP if self._gap("languages") else self.languages_data

    async def community_profile(self, client, ref):
        return COLLECTION_GAP if self._gap("community_profile") else self.community

    async def readme(self, client, ref):
        return COLLECTION_GAP if self._gap("readme") else self.readme_text

    async def file_metadata(self, client, ref, path):
        self.calls.append(("file_metadata", path))
        if path in self.gap_paths:
            return COLLECTION_GAP
        if path not in self.files:
            return None
        url = self.web_url(ref, path)
        return {"html_url": url, "size": len(self.files[path]), "download_url": self.raw_url(ref, path)}

    async def file_content(self, client, ref, path):
        self.calls.append(("file_content", path))
        if path in self.gap_paths:
            return COLLECTION_GAP
        return self.files.get(path)

    async def file_exists(self, client, ref, path):
        self.calls.append(("file_exists", path))
        if path in self.gap_paths:
            return COLLECTION_GAP
        if path in self.files:
            return self.web_url(ref, path)
        if self._has_dir(path):
            return self.web_url(ref, path, "tree")
        return None

    async def dir_listing(self, client, ref, path=""):
        if path in self.gap_paths or self._gap("dir_listing"):
            return COLLECTION_GAP
        prefix = path.rstrip("/") + "/" if path else ""
        entries, seen = [], set()
        for p in self.files:
            if not p.startswith(prefix):
                continue
            rest = p[len(prefix):]
            name = rest.split("/", 1)[0]
            if name in seen:
                continue
            seen.add(name)
            full = prefix + name
            kind = "dir" if "/" in rest else "file"
            entries.append({"name": name, "path": full, "html_url": self.web_url(ref, full),
                            "size": 0, "type": kind})
        return entries

    async def repo_tree(self, client, ref):
        if self._gap("repo_tree"):
            return COLLECTION_GAP
        return {"files": [{"path": p, "size": len(t)} for p, t in self.files.items()]
                + [{"path": d.rstrip("/") + "/.keep", "size": 0} for d in self.dirs],
                "truncated": self.tree_truncated}

    # ------------------------------------------------------------ history

    async def releases(self, client, ref, *, per_page=30, page=1):
        if self._gap("releases"):
            return COLLECTION_GAP
        start = (page - 1) * per_page
        return self.release_list[start:start + per_page]

    async def tags(self, client, ref, *, per_page=30, page=1):
        if self._gap("tags"):
            return COLLECTION_GAP
        start = (page - 1) * per_page
        return self.tag_list[start:start + per_page]

    async def version_tags(self, client, ref):
        return [] if self._gap("version_tags") else list(self.version_tag_list)

    async def wiki_has_content(self, client, ref):
        return self.wiki

    async def commits(self, client, ref, *, per_page=100, page=1, since=None, until=None, path=None):
        if self._gap("commits"):
            return COLLECTION_GAP
        start = (page - 1) * per_page
        return self.commit_list[start:start + per_page]

    async def first_commit_date(self, client, ref):
        return self.first_commit

    async def contributors(self, client, ref, *, per_page=100, page=1):
        if self._gap("contributors"):
            return COLLECTION_GAP
        start = (page - 1) * per_page
        return self.contributor_list[start:start + per_page]

    async def commit_participation(self, client, ref):
        return self.participation

    async def contributor_weekly_stats(self, client, ref):
        return self.weekly_stats

    async def user(self, client, login):
        if self._gap("user"):
            return COLLECTION_GAP
        return self.users.get(login)

    # ------------------------------------------------------------ issues

    async def issues(self, client, ref, *, state="all", per_page=100, page=1, sort=None, direction=None):
        if self._gap("issues"):
            return COLLECTION_GAP
        start = (page - 1) * per_page
        return self.issue_list[start:start + per_page]

    async def pull_requests(self, client, ref, *, state="all", per_page=100, page=1, sort=None, direction=None):
        if self._gap("pull_requests"):
            return COLLECTION_GAP
        start = (page - 1) * per_page
        return self.pr_list[start:start + per_page]

    async def issue_comments(self, client, ref, number, *, per_page=10):
        return self.comments.get(number, [])

    async def pr_reviews(self, client, ref, number, *, per_page=1):
        return []

    async def search_issues(self, client, query, *, per_page=1):
        self.calls.append(("search_issues", query))
        if callable(self.search):
            return self.search(query)
        return self.search.get(query, 0)

    async def issues_opened_between(self, client, ref, start, end, *, per_page=100):
        return self.opened_between

    async def issues_closed_between(self, client, ref, start, end):
        return self.closed_between

    async def labels(self, client, ref, *, page=1, per_page=100):
        if self._gap("labels"):
            return COLLECTION_GAP
        start = (page - 1) * per_page
        return self.label_list[start:start + per_page]

    async def recent_issues(self, client, ref, since, *, page=1, per_page=100):
        if page - 1 < len(self.recent_issue_pages):
            return self.recent_issue_pages[page - 1]
        return []

    # ------------------------------------------------------------ CI

    async def ci_config_files(self, client, ref):
        if self._gap("ci_config_files"):
            return COLLECTION_GAP
        if self.ci_files is not None:
            return self.ci_files
        return [
            {"name": p.rsplit("/", 1)[-1], "path": p, "html_url": self.web_url(ref, p), "primary": False}
            for p in self.files
            if p.startswith(".github/workflows/") and p.endswith((".yml", ".yaml"))
        ]

    async def ci_runs(self, client, ref, *, branch=None, status=None, per_page=100, page=1):
        start = (page - 1) * per_page
        return self.ci_run_list[start:start + per_page]

    async def ci_workflows(self, client, ref):
        return COLLECTION_GAP if self._gap("ci_workflows") else self.workflows

    async def ci_workflow_runs(self, client, ref, workflow_id, *, per_page=100):
        return []

    async def deployments(self, client, ref, *, per_page=100, page=1):
        return self.deployment_list

    async def deployment_succeeded(self, client, statuses_url):
        return False


def gitlab_fake(**kwargs: Any) -> FakeForge:
    """A FakeForge that reports itself as a GitLab instance."""
    fake = FakeForge(**kwargs)
    fake.platform = "gitlab"
    fake.host = "gitlab.example.com"
    fake.display_name = "GitLab"
    fake.PLATFORM_PATHS = {
        "codeowners": [".gitlab/CODEOWNERS"],
        "issue_templates": [".gitlab/issue_templates"],
        "change_request_templates": [".gitlab/merge_request_templates"],
        "dependency_automation": [".gitlab/renovate.json"],
        "security_scan_workflows": [],
    }
    return fake


# GitHub's platform paths, so collectors see the same locations as in production.
from forge.github import GitHubForge  # noqa: E402

FakeForge.PLATFORM_PATHS = GitHubForge.PLATFORM_PATHS
