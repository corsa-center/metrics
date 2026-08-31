"""
Usability Collector (CASS Report Section 4.3.4)

Covers Documentation Completeness Analysis: whether a user arriving at the
repository can find out how to install the software, how to use it, and where
the full documentation lives.

Installation Success Tracking for this section is produced by
`collectors/sustainability/collaboration.py`, which already queries the package
registries; the orchestrator renders it here rather than repeating that lookup.

Not collected: User Experience Assessment (the report specifies the UEQ
instrument, which needs a survey), Accessibility Feature Detection and Usage
Analytics Integration.
"""

import asyncio
import base64
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx

from collectors.sustainability.base import GitHubCollectorBase

logger = logging.getLogger(__name__)

# README headings that answer a new user's first questions. Matched against
# heading text only, so a passing mention in a paragraph doesn't count.
_README_SECTIONS = {
    "Installation": r"(?:install|building|build from source|getting started|setup)",
    "Usage": r"(?:usage|using|quick ?start|how to use|basic use|tutorial)",
    "Examples": r"(?:examples?|demos?|sample)",
    "Support": r"(?:support|help|contact|community|questions|mailing list)",
}

_DOC_DIRECTORIES = ["docs", "doc", "documentation", "Documentation"]

# Markdown ATX headings and Setext underlines both appear in real READMEs.
_ATX_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
_SETEXT_HEADING = re.compile(r"^\s{0,3}(\S.*)\n\s{0,3}[=-]{3,}\s*$", re.MULTILINE)

# Most of the four questions answered is a complete-enough README.
_MIN_README_SECTIONS = 3


class UsabilityCollector(GitHubCollectorBase):
    """Collects documentation completeness signals (Section 4.3.4)."""

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        repo_name = package.get("name", "Unknown")
        owner_repo = self._extract_owner_repo(package.get("repo_url", ""))
        if not owner_repo:
            logger.error(f"Could not extract owner/repo from {package.get('repo_url')}")
            return self._empty_result(repo_name)

        owner, repo = owner_repo
        logger.info(f"Collecting usability metrics for {repo_name}")

        async with httpx.AsyncClient(timeout=30.0) as client:
            readme, doc_dir, site = await asyncio.gather(
                self._analyze_readme(client, owner, repo),
                self._find_doc_directory(client, owner, repo),
                self._find_documentation_site(client, owner, repo),
                return_exceptions=True,
            )

        if isinstance(readme, Exception):
            logger.warning(f"README analysis failed: {readme}")
            readme = {"exists": False, "sections": [], "missing": list(_README_SECTIONS)}
        if isinstance(doc_dir, Exception):
            logger.warning(f"Doc directory scan failed: {doc_dir}")
            doc_dir = None
        if isinstance(site, Exception):
            logger.warning(f"Documentation site lookup failed: {site}")
            site = None

        return {
            "package_name": repo_name,
            "repository": f"{owner}/{repo}",
            "timestamp": self._get_timestamp(),
            "readme": readme,
            "doc_directory": doc_dir,
            "documentation_site": site,
            "overall_score": self._calculate_score(readme, doc_dir, site),
        }

    async def _analyze_readme(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> Dict[str, Any]:
        """Which of the core user questions the README's headings answer."""
        resp = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}/readme",
            headers=self.github_headers,
        )
        if resp.status_code != 200:
            return {"exists": False, "sections": [], "missing": list(_README_SECTIONS)}

        text = base64.b64decode(resp.json().get("content", "")).decode("utf-8", "replace")
        headings = _ATX_HEADING.findall(text) + _SETEXT_HEADING.findall(text)

        found = [
            label
            for label, pattern in _README_SECTIONS.items()
            if any(re.search(pattern, h, re.IGNORECASE) for h in headings)
        ]
        return {
            "exists": True,
            "length": len(text),
            "heading_count": len(headings),
            "sections": found,
            "missing": [s for s in _README_SECTIONS if s not in found],
        }

    async def _find_doc_directory(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> Optional[str]:
        """First documentation directory present in the repository."""
        for path in _DOC_DIRECTORIES:
            url = await self._check_file_exists(client, owner, repo, path)
            if url:
                return path
        return None

    async def _find_documentation_site(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> Optional[Dict[str, str]]:
        """A published documentation site, from GitHub Pages or the homepage."""
        resp = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}", headers=self.github_headers
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        homepage = (data.get("homepage") or "").strip()
        if homepage:
            return {"url": homepage, "source": "repository homepage"}
        if data.get("has_pages"):
            return {"url": f"https://{owner}.github.io/{repo}/", "source": "GitHub Pages"}
        return None

    def _calculate_score(
        self, readme: Dict, doc_dir: Optional[str], site: Optional[Dict]
    ) -> Dict[str, Any]:
        sections = readme.get("sections", [])
        parts = [f"README covers {len(sections)}/{len(_README_SECTIONS)} core sections"]
        if doc_dir:
            parts.append(f"{doc_dir}/ present")
        if site:
            parts.append("documentation site published")

        # A complete README, or a thinner one backed by real documentation
        # elsewhere, both count as documented.
        complete = len(sections) >= _MIN_README_SECTIONS or (
            bool(sections) and bool(doc_dir) and bool(site)
        )

        sub: Dict[str, Dict[str, Any]] = {
            "documentation_completeness": {
                "label": "Documentation Completeness Analysis",
                "value": "; ".join(parts),
                "detail": ", ".join(sections) if sections else None,
                "passing": complete,
            }
        }
        for key, label in [
            ("user_experience", "User Experience Assessment"),
            ("accessibility_features", "Accessibility Feature Detection"),
            ("usage_analytics", "Usage Analytics Integration"),
        ]:
            sub[key] = {"label": label, "value": None, "passing": False, "not_collected": True}

        score = sum(1 for s in sub.values() if s.get("passing"))
        return {
            "score": score,
            "max_score": len(sub),
            "percentage": round(score / len(sub) * 100, 2),
            "sub_scores": sub,
        }

    def _empty_result(self, repo_name: str) -> Dict[str, Any]:
        readme = {"exists": False, "sections": [], "missing": list(_README_SECTIONS)}
        return {
            "package_name": repo_name,
            "repository": "unknown",
            "timestamp": self._get_timestamp(),
            "readme": readme,
            "doc_directory": None,
            "documentation_site": None,
            "overall_score": self._calculate_score(readme, None, None),
        }
