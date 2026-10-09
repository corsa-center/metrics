"""
Usability Collector (CASS Report Section 4.3.4)

Covers Documentation Completeness Analysis: whether a user arriving at the
repository can find out how to install the software, how to use it, and where
the full documentation lives.

Installation Success Tracking for this section is produced by
`collectors/ecosystem/collaboration.py`, which already queries the package
registries; the orchestrator renders it here rather than repeating that lookup.

Not collected: User Experience Assessment (the report specifies the UEQ
instrument, which needs a survey), Accessibility Feature Detection and Usage
Analytics Integration.
"""

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx

from collectors.ecosystem.base import COLLECTION_GAP, RepoTree, RetryingTransport, get_threshold
from forge.interface import Forge

logger = logging.getLogger(__name__)

# README headings that answer a new user's first questions. Matched against
# heading text only, so a passing mention in a paragraph doesn't count.
_README_SECTIONS = {
    "Installation": r"(?:install|build(?:ing)?\b|getting started|set ?up\b)",
    "Usage": r"(?:usage|using|quick ?start|how to use|basic use|tutorial)",
    "Examples": r"(?:examples?|demos?|sample)",
    "Support": r"(?:support|help|contact|community|questions|mailing list|who (?:do|to) (?:i )?talk to)",
}

_DOC_DIRECTORIES = ["docs", "doc", "documentation"]

# Markdown ATX headings and Setext underlines both appear in real READMEs.
_ATX_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
# Underlines also in reStructuredText's other adornment characters.
_SETEXT_HEADING = re.compile(
    r"^\s{0,3}(\S.*)\n\s{0,3}(?:={3,}|-{3,}|~{3,}|\^{3,}|\*{3,}|#{3,}|\+{3,})\s*$", re.MULTILINE)
# Plain-text READMEs with no markup often mark sections "* Quick start".
_PLAIN_HEADING = re.compile(r"^\* ([A-Z][^\n*]{2,60})\n\s*\n", re.MULTILINE)
# Link text counts too: a README can cover a topic with a link instead of a
# section ("[Getting Started](...)", "[Contact Us](...)"). Image and badge
# links (![...]) are excluded.
_LINK_TEXT = re.compile(r"(?<!!)\[(?!!)([^\]\n]{3,80})\]\(")


def readme_covers(text: str, label: str) -> bool:
    """Whether README markdown covers one of _README_SECTIONS, by a heading or
    by link text starting with the topic. Anchored at the start for links: a
    link reading "Building X using Spack" is an install guide, not usage."""
    return readme_mentions(text, _README_SECTIONS[label])


def readme_mentions(text: str, pattern: str) -> bool:
    """Whether a README heading, or link text starting with the topic,
    matches pattern."""
    atx = _ATX_HEADING.findall(text)
    headings = atx + _SETEXT_HEADING.findall(text)
    if not atx:
        headings += _PLAIN_HEADING.findall(text)
    return (any(re.search(pattern, h, re.IGNORECASE) for h in headings)
            or any(re.match(rf"\W*{pattern}", t, re.IGNORECASE) for t in _LINK_TEXT.findall(text)))


class UsabilityCollector:
    """Collects documentation completeness signals (Section 4.3.4)."""

    def __init__(self, forge: Forge):
        self.forge = forge

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        repo_name = package.get("name", "Unknown")
        ref = self.forge.extract_ref(package.get("repo_url", ""))
        if not ref:
            logger.error(f"Could not extract a repo reference from {package.get('repo_url')}")
            return self._empty_result(repo_name)

        logger.info(f"Collecting usability metrics for {repo_name}")

        async with httpx.AsyncClient(timeout=30.0, transport=RetryingTransport()) as client:
            readme, tree, (site, site_gap) = await asyncio.gather(
                self._analyze_readme(client, ref),
                RepoTree.fetch(client, self.forge, ref),
                self._find_documentation_site(client, ref),
                return_exceptions=False,
            )
        doc_dir, doc_dir_gap = self._find_doc_directory(tree)

        return {
            "package_name": repo_name,
            "repository": ref,
            "timestamp": self.forge.get_timestamp(),
            "readme": readme,
            "doc_directory": doc_dir,
            "documentation_site": site,
            "overall_score": self._calculate_score(
                readme, doc_dir, site, doc_dir_gap or site_gap
            ),
        }

    async def _analyze_readme(
        self, client: httpx.AsyncClient, ref: str
    ) -> Dict[str, Any]:
        """Which of the core user questions the README's headings answer."""
        text = await self.forge.readme(client, ref)
        if text is COLLECTION_GAP:
            return {
                "exists": False, "sections": [], "missing": list(_README_SECTIONS),
                "not_collected": True,
            }
        if text is None:
            return {"exists": False, "sections": [], "missing": list(_README_SECTIONS)}

        found = [label for label in _README_SECTIONS if readme_covers(text, label)]
        return {
            "exists": True,
            "length": len(text),
            "heading_count": len(_ATX_HEADING.findall(text) + _SETEXT_HEADING.findall(text)),
            "sections": found,
            "missing": [s for s in _README_SECTIONS if s not in found],
        }

    def _find_doc_directory(self, tree) -> tuple:
        """First documentation directory present in the repository, and
        whether the tree fetch gapped rather than confirming absence.

        Matched via RepoTree.has_dir(), which is case-insensitive -- AMReX's
        "Docs" and superlu's "DOC" both match a candidate spelled "docs"
        without needing every casing enumerated here (corsa-center/metrics#54).
        """
        if tree is COLLECTION_GAP:
            return None, True
        for name in _DOC_DIRECTORIES:
            if tree.has_dir(name):
                return name, False
        return None, False

    async def _find_documentation_site(
        self, client: httpx.AsyncClient, ref: str
    ) -> tuple:
        """A published documentation site, from GitHub Pages or the homepage."""
        data = await self.forge.repo_info(client, ref)
        if data is COLLECTION_GAP:
            return None, True
        if data is None:
            return None, False
        homepage = (data.get("homepage") or "").strip()
        if homepage:
            return {"url": homepage, "source": "repository homepage"}, False
        if data.get("has_pages"):
            return {"url": self.forge.pages_url(ref), "source": f"{self.forge.display_name} Pages"}, False
        return None, False

    def _calculate_score(
        self, readme: Dict, doc_dir: Optional[str], site: Optional[Dict],
        has_gap: bool = False,
    ) -> Dict[str, Any]:
        sections = readme.get("sections", [])
        parts = [f"README covers {len(sections)}/{len(_README_SECTIONS)} core sections"]
        if doc_dir:
            parts.append(f"{doc_dir}/ present")
        if site:
            parts.append("documentation site published")

        # A complete README, or a thinner one backed by real documentation
        # elsewhere, both count as documented.
        complete = len(sections) >= get_threshold("4.3.4", "Documentation Completeness Analysis") or (
            bool(sections) and bool(doc_dir) and bool(site)
        )

        doc_entry: Dict[str, Any] = {
            "label": "Documentation Completeness Analysis",
            "value": "; ".join(parts),
            "detail": ", ".join(sections) if sections else None,
            "passing": complete,
        }
        # A negative result built on a gap (README fetch failed, or the doc
        # directory/site lookup that the fallback path needs didn't come
        # back) isn't a confirmed absence -- the gap could be hiding the
        # section, directory, or site that would have made this pass. A
        # positive result stands regardless: it was reached using data that
        # did come back.
        if not complete and (has_gap or readme.get("not_collected")):
            doc_entry["not_collected"] = True

        sub: Dict[str, Dict[str, Any]] = {"documentation_completeness": doc_entry}
        for key, label in [
            ("user_experience", "User Experience Assessment"),
            ("accessibility_features", "Accessibility Feature Detection"),
            ("usage_analytics", "Usage Analytics Integration"),
        ]:
            sub[key] = {"label": label, "value": None, "passing": False, "not_collected": True}

        scorable = {k: v for k, v in sub.items() if not v.get("not_collected")}
        score = sum(1 for s in scorable.values() if s.get("passing"))
        max_score = len(scorable)
        if not max_score:
            return {
                "score": None, "max_score": 0, "percentage": None,
                "status": "not_collected", "sub_scores": sub,
            }
        return {
            "score": score,
            "max_score": max_score,
            "percentage": round(score / max_score * 100, 2),
            "sub_scores": sub,
        }

    def _empty_result(self, repo_name: str) -> Dict[str, Any]:
        readme = {"exists": False, "sections": [], "missing": list(_README_SECTIONS)}
        return {
            "package_name": repo_name,
            "repository": "unknown",
            "timestamp": self.forge.get_timestamp(),
            "readme": readme,
            "doc_directory": None,
            "documentation_site": None,
            "overall_score": self._calculate_score(readme, None, None),
        }
