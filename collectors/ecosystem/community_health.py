"""
Community Health Metrics Collector

Collects metrics related to community health including:
- Code of Conduct (CoC)
- Governance documentation
- Contributor Guidelines
- Community documentation
"""

import asyncio
import httpx
import logging
from typing import Dict, Any, Optional, List
from pathlib import Path
import re

from collectors.ecosystem.base import COLLECTION_GAP, RetryingTransport, get_threshold

logger = logging.getLogger(__name__)


class CommunityHealthCollector:
    """Collects community health metrics from GitHub repositories"""

    # Common file patterns for community documents
    COC_PATTERNS = [
        "CODE_OF_CONDUCT.md",
        "CODE_OF_CONDUCT.txt",
        "CODE_OF_CONDUCT.rst",
        "CODE-OF-CONDUCT.md",
        "code_of_conduct.md",
        "code-of-conduct.md",
        "coc.md",
        "CoC.md",
        "CODE_OF_CONDUCT",
        "docs/CODE_OF_CONDUCT.md",
        "docs/CODE_OF_CONDUCT.rst",
        ".github/CODE_OF_CONDUCT.md",
    ]

    GOVERNANCE_PATTERNS = [
        "GOVERNANCE.md",
        "GOVERNANCE.txt",
        "GOVERNANCE.rst",
        "governance.md",
        "governance.rst",
        "docs/GOVERNANCE.md",
        "docs/governance.md",
        "docs/GOVERNANCE.rst",
        "docs/governance.rst",
        ".github/GOVERNANCE.md",
        "GOVERNANCE",
        "project-governance.md",
        "PROJECT_GOVERNANCE.md",
    ]

    CONTRIBUTING_PATTERNS = [
        "CONTRIBUTING.md",
        "CONTRIBUTING.txt",
        "CONTRIBUTING.rst",
        "contributing.md",
        "CONTRIBUTING",
        "docs/CONTRIBUTING.md",
        "docs/contributing.md",
        "docs/CONTRIBUTING.rst",
        ".github/CONTRIBUTING.md",
        "CONTRIBUTE.md",
        "contribute.md",
        "docs/contribute.md",
    ]

    # Vocabulary that indicates a documented decision-making process rather
    # than a document that merely exists.
    GOVERNANCE_KEYWORDS = {
        "Decision process": [
            "consensus", "vote", "voting", "quorum", "majority", "veto",
            "decision-making", "decision making", "rfc",
        ],
        "Defined roles": [
            "maintainer", "committer", "steering", "technical committee", "tsc",
            "core team", "reviewer", "triager", "working group",
        ],
        "Membership lifecycle": [
            "nomination", "nominate", "onboarding", "offboarding", "emeritus",
            "stepping down", "become a maintainer", "promotion",
        ],
        "Conflict resolution": [
            "escalat", "dispute", "conflict resolution", "appeal", "enforcement",
            "code of conduct committee",
        ],
    }

    CODEOWNERS_PATHS = [
        "CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS",
    ]

    # Some projects keep governance material in a dedicated sibling repo
    # rather than co-located with the code -- Kokkos among them: kokkos/kokkos
    # has neither GOVERNANCE.md nor any link to kokkos/governance (confirmed
    # via GitHub code search -- the CoC/Contributing docs it does have link to
    # kokkos.org pages, not the sibling repo), but kokkos/governance itself
    # has GOVERNANCE.md, code-of-conduct.md, and a technical charter. These
    # are conventional enough names to check automatically, with no per-
    # project configuration needed, whenever the primary repo comes up short.
    # A project with a differently-named governance repo still has the
    # existing package_config overrides as an escape hatch.
    FALLBACK_REPO_NAMES = ["governance", ".github"]

    # Keyword groups needed before the documented process counts as substantive.
    MIN_KEYWORD_GROUPS = 2

    def __init__(self, github_token: Optional[str] = None):
        """Initialize collector with optional GitHub token"""
        self.github_token = github_token
        self.headers = {"Accept": "application/vnd.github.v3+json"}
        if github_token:
            self.headers["Authorization"] = f"token {github_token}"

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        """
        Collect community health metrics for a package

        Args:
            package: Dictionary with 'name' and 'repo_url' keys

        Returns:
            Dictionary with community health metrics
        """
        repo_name = package.get("name", "Unknown")
        repo_url = package.get("repo_url", "")

        logger.info(f"Collecting community health metrics for {repo_name}")

        # Extract owner/repo from URL
        owner_repo = self._extract_owner_repo(repo_url)
        if not owner_repo:
            logger.error(f"Could not extract owner/repo from {repo_url}")
            return self._empty_result(repo_name)

        owner, repo = owner_repo

        # One listing of the directories these documents live in, matched
        # case-insensitively, instead of guessing spellings one request at a time.
        index, has_gap = await self._build_file_index(owner, repo)
        coc_result = self._match_pattern(index, self.COC_PATTERNS, owner, repo, has_gap)
        governance_result = self._match_pattern(index, self.GOVERNANCE_PATTERNS, owner, repo, has_gap)
        contributing_result = self._match_pattern(index, self.CONTRIBUTING_PATTERNS, owner, repo, has_gap)

        coc_result, governance_result, contributing_result = await self._check_fallback_repos(
            owner, coc_result, governance_result, contributing_result
        )

        # Get community profile from GitHub API (if token available)
        community_profile = await self._get_community_profile(owner, repo)

        # Section 4.2.2 asks not just whether these documents exist but whether
        # they describe a real process and are still being maintained. Each
        # document carries its own "repository" now (it may have come from a
        # fallback repo above), so these read from that rather than assuming
        # everything lives in the primary repo.
        keyword_analysis = await self._analyze_governance_keywords(
            [governance_result, contributing_result, coc_result]
        )
        effectiveness = await self._assess_effectiveness(
            owner, repo, [governance_result, contributing_result]
        )

        return {
            "package_name": repo_name,
            "repository": f"{owner}/{repo}",
            "timestamp": self._get_timestamp(),
            "code_of_conduct": coc_result,
            "governance": governance_result,
            "contributing_guidelines": contributing_result,
            "community_profile": community_profile,
            "keyword_analysis": keyword_analysis,
            "effectiveness": effectiveness,
            "overall_score": self._calculate_score(
                coc_result, governance_result, contributing_result
            ),
        }

    async def _analyze_governance_keywords(
        self, documents: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Which decision-making concepts the governance documents actually cover.

        Reads the full documents rather than the 200-character preview kept for
        display — a preview is the title and a sentence, which says nothing about
        whether a decision process is written down.

        Each document carries its own "repository" (usually the primary repo,
        but a fallback one like {org}/governance when that's where it was
        actually found), so this reads each from wherever it really lives
        rather than assuming they're all co-located.
        """
        located = [
            (d["repository"].split("/", 1)[0], d["repository"].split("/", 1)[1], d["file_path"])
            for d in documents if d.get("exists") and d.get("file_path") and d.get("repository")
        ]
        if not located:
            return {"groups_found": [], "documents_read": 0}

        texts = await asyncio.gather(
            *[self._get_file_text(o, r, p) for o, r, p in located],
            return_exceptions=True,
        )
        corpus = " ".join(
            t.lower() for t in texts if isinstance(t, str) and t
        )
        if not corpus:
            return {"groups_found": [], "documents_read": 0}

        found = [
            group for group, terms in self.GOVERNANCE_KEYWORDS.items()
            if any(term in corpus for term in terms)
        ]
        return {"groups_found": found, "documents_read": len(located)}

    async def _assess_effectiveness(
        self, owner: str, repo: str, documents: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Whether governance is live: owners assigned and documents maintained.

        CODEOWNERS is checked on the primary repo only -- it names people
        with review authority over *this* repo's code, so a fallback repo's
        CODEOWNERS (if it even has one) wouldn't mean anything here. Document
        staleness, in contrast, reads each document from wherever it was
        actually found, same as _analyze_governance_keywords.
        """
        located = [
            (d["repository"].split("/", 1)[0], d["repository"].split("/", 1)[1], d["file_path"])
            for d in documents if d.get("exists") and d.get("file_path") and d.get("repository")
        ]

        has_codeowners = False
        for path in self.CODEOWNERS_PATHS:
            if (await self._check_file_exists(owner, repo, path)).get("exists"):
                has_codeowners = True
                break

        last_updated_days = None
        if located:
            ages = await asyncio.gather(
                *[self._days_since_last_change(o, r, p) for o, r, p in located],
                return_exceptions=True,
            )
            valid = [a for a in ages if isinstance(a, int)]
            if valid:
                last_updated_days = min(valid)

        maintained = (
            last_updated_days is not None
            and last_updated_days <= get_threshold("4.2.1", "Governance Effectiveness Assessment", "stale_days")
        )
        return {
            "has_codeowners": has_codeowners,
            "days_since_governance_update": last_updated_days,
            "maintained": maintained,
        }

    async def _github_get(self, url: str, params: Optional[dict] = None):
        """GET a GitHub API endpoint. Returns the parsed body, None for a
        confirmed 404, or COLLECTION_GAP if we couldn't actually tell
        (rate limit, network error, other non-2xx).

        Every method below used to make this call inline with a bare
        `if status != 200: return <empty>` — which silently turned a GitHub
        secondary rate limit (403, common under this pipeline's concurrent
        per-package bursts) into "this file doesn't exist" instead of
        retrying. That's what made kokkos/kokkos's CoC/governance docs (which
        do exist, under docs/) read as "not found" on the dashboard.
        Retrying is now RetryingTransport's job (below), transparent to this
        method — it only needs to interpret whatever the final response is.

        COLLECTION_GAP is falsy, same as None, so `if not data:` keeps
        working unchanged for callers that haven't opted into the
        distinction; `if data is COLLECTION_GAP:` is for ones that have.
        """
        try:
            async with httpx.AsyncClient(timeout=30.0, transport=RetryingTransport()) as client:
                response = await client.get(url, headers=self.headers, params=params)
        except Exception as e:
            # COLLECTION-GAP: grep-able tag for "why is this metric empty"
            # -- see the matching tag in collectors/ecosystem/base.py.
            logger.warning(f"COLLECTION-GAP url={url} status=exception reason={e!r}")
            return COLLECTION_GAP
        if response.status_code == 200:
            return response.json()
        if response.status_code == 404:
            return None
        return COLLECTION_GAP

    async def _get_file_text(self, owner: str, repo: str, path: str) -> str:
        """Full decoded text of a repository file."""
        data = await self._github_get(f"https://api.github.com/repos/{owner}/{repo}/contents/{path}")
        if not data:
            return ""
        import base64
        return base64.b64decode(data.get("content", "")).decode("utf-8", "replace")

    async def _days_since_last_change(
        self, owner: str, repo: str, path: str
    ) -> Optional[int]:
        """Days since the most recent commit touching a given path."""
        data = await self._github_get(
            f"https://api.github.com/repos/{owner}/{repo}/commits",
            params={"path": path, "per_page": 1},
        )
        if not data:
            return None
        from datetime import datetime, timezone
        when = data[0]["commit"]["committer"]["date"]
        dt = datetime.fromisoformat(when.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).days

    async def _list_dir(self, owner: str, repo: str, path: str = "") -> Dict[str, Dict]:
        """Directory listing keyed by lower-cased path, for case-insensitive
        lookup. Returns COLLECTION_GAP (not an empty dict) if the listing
        itself couldn't be fetched -- an empty dict must only ever mean
        "this directory has no files", not "we don't know what's in it".

        GitHub's Contents API is case-sensitive, so a pattern list can only ever
        match the spellings someone thought to enumerate. ADIOS2 names its guide
        `Contributing.md`, which no reasonable list of upper/lower variants
        catches, and the file was invisible to this collector.
        """
        entries = await self._github_get(
            f"https://api.github.com/repos/{owner}/{repo}/contents/{path}".rstrip("/")
        )
        if entries is COLLECTION_GAP:
            return COLLECTION_GAP
        if not isinstance(entries, list):
            return {}
        prefix = f"{path}/" if path else ""
        return {
            f"{prefix}{e['name']}".lower(): e
            for e in entries if e.get("type") == "file"
        }

    async def _build_file_index(self, owner: str, repo: str) -> tuple:
        """Case-insensitive index of the directories community docs live in,
        plus whether any of the three listings that feed it gapped.

        A gapped listing is dropped from the merged index rather than
        raising -- the other two listings' real data is still worth having
        -- but the caller needs to know coverage was incomplete, since
        "not in the index" now might mean "wasn't listed", not "doesn't
        exist". Returns (index, has_gap).
        """
        listings = await asyncio.gather(
            self._list_dir(owner, repo),
            self._list_dir(owner, repo, ".github"),
            self._list_dir(owner, repo, "docs"),
            return_exceptions=True,
        )
        index: Dict[str, Dict] = {}
        has_gap = False
        for listing in listings:
            if listing is COLLECTION_GAP or isinstance(listing, Exception):
                has_gap = True
            elif isinstance(listing, dict):
                index.update(listing)
        return index, has_gap

    def _match_pattern(
        self, index: Dict[str, Dict], patterns: List[str], owner: str, repo: str,
        has_gap: bool = False,
    ) -> Dict[str, Any]:
        """First pattern present in the index, compared case-insensitively.

        Carries which repo this index came from on every hit, since it may
        be a fallback repo (see _check_fallback_repos) rather than the
        primary one -- callers that read the file's own content
        (_analyze_governance_keywords, _assess_effectiveness) need to know
        where to actually fetch it from.

        A positive result (found in the index) is trustworthy even if
        has_gap is True -- finding it means at least one of the underlying
        listings succeeded and had it, regardless of whether another one
        failed. A negative result under has_gap is NOT trustworthy: the
        file could be sitting in whichever directory listing didn't come
        back, so this reports not_collected instead of a confident "absent".
        """
        for pattern in patterns:
            entry = index.get(pattern.lower())
            if entry:
                return {
                    "exists": True,
                    "file_path": entry.get("path", pattern),
                    "url": entry.get("html_url", ""),
                    "size": entry.get("size", 0),
                    "content_preview": "",
                    "repository": f"{owner}/{repo}",
                }
        if has_gap:
            return {"exists": False, "file_path": None, "url": None, "repository": None,
                     "not_collected": True}
        return {"exists": False, "file_path": None, "url": None, "repository": None}

    async def _check_fallback_repos(
        self,
        owner: str,
        coc_result: Dict[str, Any],
        governance_result: Dict[str, Any],
        contributing_result: Dict[str, Any],
    ) -> tuple:
        """Retry whichever of CoC/Governance/Contributing came up empty
        against FALLBACK_REPO_NAMES, e.g. {owner}/governance.

        Kokkos is the confirmed case: kokkos/kokkos has neither GOVERNANCE.md
        nor a link to kokkos/governance anywhere in it (checked via GitHub
        code search), but kokkos/governance has GOVERNANCE.md,
        code-of-conduct.md, and a technical charter. Only runs when at least
        one of the three is still missing, and stops as soon as all three are
        resolved, to avoid spending requests on projects that don't need this.
        """
        pending = {
            "coc": (self.COC_PATTERNS, coc_result),
            "governance": (self.GOVERNANCE_PATTERNS, governance_result),
            "contributing": (self.CONTRIBUTING_PATTERNS, contributing_result),
        }
        missing = {k for k, (_, r) in pending.items() if not r["exists"]}
        if not missing:
            return coc_result, governance_result, contributing_result

        results = {"coc": coc_result, "governance": governance_result, "contributing": contributing_result}
        for fallback_repo in self.FALLBACK_REPO_NAMES:
            if not missing:
                break
            fb_index, fb_has_gap = await self._build_file_index(owner, fallback_repo)
            if not fb_index and not fb_has_gap:
                continue  # repo doesn't exist or genuinely has none of these files
            for key in list(missing):
                patterns, _ = pending[key]
                match = self._match_pattern(fb_index, patterns, owner, fallback_repo, fb_has_gap)
                if match["exists"]:
                    logger.info(f"{key} found in fallback repo {owner}/{fallback_repo}")
                    results[key] = match
                    missing.discard(key)
                elif match.get("not_collected"):
                    # Still unresolved, but now know it's specifically
                    # because the fallback repo's own listing gapped --
                    # worth reporting that instead of the primary repo's
                    # possibly-different not_collected/absent result.
                    results[key] = match

        return results["coc"], results["governance"], results["contributing"]

    async def _check_file_exists(
        self, owner: str, repo: str, file_path: str
    ) -> Dict[str, Any]:
        """
        Check if a file exists in the repository

        Args:
            owner: Repository owner
            repo: Repository name
            file_path: Path to file to check

        Returns:
            Dictionary with exists, url, size, and optional content_preview
        """
        data = await self._github_get(f"https://api.github.com/repos/{owner}/{repo}/contents/{file_path}")
        if not data:
            return {"exists": False}

        # Get content preview (first 200 chars)
        content_preview = ""
        if "download_url" in data:
            content_preview = await self._get_content_preview(data["download_url"])

        return {
            "exists": True,
            "url": data.get("html_url", ""),
            "size": data.get("size", 0),
            "content_preview": content_preview,
        }

    async def _get_content_preview(
        self, download_url: str, max_chars: int = 200
    ) -> str:
        """Get preview of file content.

        Served from raw.githubusercontent.com, not api.github.com, so it
        isn't subject to the same secondary rate limit -- a plain best-effort
        fetch is fine here; a missing preview isn't reported as anything.
        """
        try:
            async with httpx.AsyncClient(timeout=30.0, transport=RetryingTransport()) as client:
                response = await client.get(download_url)
                if response.status_code == 200:
                    text = response.text
                    # Return first 200 chars
                    preview = text[:max_chars].strip()
                    if len(text) > max_chars:
                        preview += "..."
                    return preview
        except Exception as e:
            logger.debug(f"Error getting content preview: {e}")
        return ""

    async def _get_community_profile(self, owner: str, repo: str) -> Dict[str, Any]:
        """
        Get community profile from GitHub API
        This includes health percentage and other community metrics
        """
        if not self.github_token:
            return {}

        data = await self._github_get(f"https://api.github.com/repos/{owner}/{repo}/community/profile")
        return data or {}

    def _extract_owner_repo(self, repo_url: str) -> Optional[tuple]:
        """Extract owner and repo name from GitHub URL"""
        # Handle various GitHub URL formats
        patterns = [
            r"github\.com/([^/]+)/([^/]+)",
            r"github\.com:([^/]+)/([^/]+)",
        ]

        for pattern in patterns:
            match = re.search(pattern, repo_url)
            if match:
                owner = match.group(1)
                repo = match.group(2).replace(".git", "")
                return (owner, repo)

        return None

    def _calculate_score(
        self, coc: Dict, governance: Dict, contributing: Dict
    ) -> Dict[str, Any]:
        """Calculate overall community health score.

        A not_collected item (gap, not a confirmed absence) is dropped from
        both score and max_score, not counted as a failing ✗ -- same
        convention as chaoss_governance.py's weighted-average exclusion,
        applied here to a simple count instead.
        """
        score = 0
        max_score = 0
        details = []

        for label, result in [
            ("Code of Conduct", coc),
            ("Governance", governance),
            ("Contributing Guidelines", contributing),
        ]:
            if result.get("not_collected"):
                details.append(f"{label}: ? (not collected)")
                continue
            max_score += 1
            if result.get("exists"):
                score += 1
                details.append(f"{label}: ✓")
            else:
                details.append(f"{label}: ✗")

        return {
            "score": score,
            "max_score": max_score,
            "percentage": round((score / max_score) * 100, 2) if max_score else None,
            "details": details,
        }

    def _empty_result(self, repo_name: str) -> Dict[str, Any]:
        """Return empty result structure"""
        return {
            "package_name": repo_name,
            "repository": "unknown",
            "timestamp": self._get_timestamp(),
            "code_of_conduct": {"exists": False},
            "governance": {"exists": False},
            "contributing_guidelines": {"exists": False},
            "community_profile": {},
            "overall_score": {
                "score": 0,
                "max_score": 3,
                "percentage": 0,
                "details": [],
            },
        }

    def _get_timestamp(self) -> str:
        """Get current timestamp"""
        from datetime import datetime

        return datetime.utcnow().isoformat() + "Z"
