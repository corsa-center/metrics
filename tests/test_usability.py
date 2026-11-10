"""Unit tests for UsabilityCollector (CASS Section 4.3.4)."""

import pytest
from formatters.dashboard import DashboardFormatter

from collectors.common import COLLECTION_GAP, RepoTree
from collectors.quality.usability import UsabilityCollector, _README_SECTIONS
from tests.fakes import FakeForge
from forge.github import GitHubForge


@pytest.fixture
def collector():
    return UsabilityCollector(GitHubForge())


class TestScoring:
    def _score(self, collector, sections=(), doc_dir=None, site=None):
        readme = {"exists": True, "sections": list(sections),
                  "missing": [s for s in _README_SECTIONS if s not in sections]}
        return collector._calculate_score(readme, doc_dir, site)

    def test_three_sections_is_complete(self, collector):
        s = self._score(collector, sections=["Installation", "Usage", "Examples"])
        assert s["sub_scores"]["documentation_completeness"]["passing"]

    def test_two_sections_alone_is_not(self, collector):
        s = self._score(collector, sections=["Usage", "Support"])
        assert not s["sub_scores"]["documentation_completeness"]["passing"]

    def test_thin_readme_backed_by_real_docs_passes(self, collector):
        # HDF5's shape: a short README, but a docs/ tree and a published site.
        s = self._score(collector, sections=["Usage", "Support"],
                        doc_dir="docs", site={"url": "https://example.org"})
        assert s["sub_scores"]["documentation_completeness"]["passing"]

    def test_no_readme_sections_never_passes(self, collector):
        s = self._score(collector, sections=[], doc_dir="docs",
                        site={"url": "https://example.org"})
        assert not s["sub_scores"]["documentation_completeness"]["passing"]

    def test_value_mentions_each_signal(self, collector):
        s = self._score(collector, sections=["Usage"], doc_dir="doc",
                        site={"url": "https://example.org"})
        value = s["sub_scores"]["documentation_completeness"]["value"]
        assert "1/4 core sections" in value
        assert "doc/ present" in value
        assert "documentation site published" in value

    def test_three_submetrics_uncollected(self, collector):
        sub = self._score(collector)["sub_scores"]
        assert sum(1 for v in sub.values() if v.get("not_collected")) == 3
        # The 3 permanently-uncollected submetrics must not inflate the
        # denominator -- only documentation_completeness is ever scorable.
        assert self._score(collector)["max_score"] == 1

    def test_gapped_negative_is_not_collected_not_a_confirmed_fail(self, collector):
        readme = {"exists": False, "sections": [], "missing": list(_README_SECTIONS),
                   "not_collected": True}
        s = collector._calculate_score(readme, None, None, has_gap=False)
        entry = s["sub_scores"]["documentation_completeness"]
        assert entry["passing"] is False
        assert entry["not_collected"] is True
        assert s["max_score"] == 0
        assert s["status"] == "not_collected"

    def test_doc_dir_or_site_gap_taints_a_negative_result(self, collector):
        readme = {"exists": True, "sections": ["Usage"], "missing": []}
        s = collector._calculate_score(readme, None, None, has_gap=True)
        entry = s["sub_scores"]["documentation_completeness"]
        assert entry["passing"] is False
        assert entry["not_collected"] is True

    def test_positive_result_survives_a_gap(self, collector):
        # 3+ sections alone is enough to pass, regardless of whether the
        # doc-dir/site lookups gapped.
        readme = {"exists": True, "sections": ["Installation", "Usage", "Examples"],
                   "missing": []}
        s = collector._calculate_score(readme, None, None, has_gap=True)
        entry = s["sub_scores"]["documentation_completeness"]
        assert entry["passing"] is True
        assert "not_collected" not in entry


class TestHeadingDetection:
    def _sections(self, collector, markdown):
        import re
        from collectors.quality.usability import _ATX_HEADING, _SETEXT_HEADING
        headings = _ATX_HEADING.findall(markdown) + _SETEXT_HEADING.findall(markdown)
        return [
            label for label, pattern in _README_SECTIONS.items()
            if any(re.search(pattern, h, re.IGNORECASE) for h in headings)
        ]

    def test_atx_headings(self, collector):
        assert "Installation" in self._sections(collector, "# Intro\n## Installation\ntext")

    def test_setext_headings(self, collector):
        assert "Usage" in self._sections(collector, "Usage\n-----\nsome text")

    def test_body_mentions_do_not_count(self, collector):
        # "install" in a paragraph isn't a documented installation section.
        assert self._sections(collector, "# Intro\nYou can install it somehow.") == []

    def test_synonyms_match(self, collector):
        assert "Installation" in self._sections(collector, "## Getting Started")
        assert "Usage" in self._sections(collector, "## Quick Start")


class TestEmptyResult:
    def test_invalid_url(self, collector):
        import asyncio
        r = asyncio.run(collector.collect({"name": "x", "repo_url": "nope"}))
        assert r["readme"]["exists"] is False
        assert r["overall_score"]["score"] == 0


class TestAnalyzeReadmeGapHandling:
    def _run(self, collector, client):
        import asyncio
        return asyncio.run(collector._analyze_readme(client, "o/r"))

    def test_gap_is_not_collected_not_a_confirmed_missing_readme(self, collector):
        from unittest.mock import AsyncMock, patch
        with patch.object(collector.forge, "_github_get", new=AsyncMock(return_value=COLLECTION_GAP)):
            result = self._run(collector, None)
        assert result["exists"] is False
        assert result["not_collected"] is True

    def test_confirmed_404_is_a_real_negative(self, collector):
        from unittest.mock import AsyncMock, patch
        with patch.object(collector.forge, "_github_get", new=AsyncMock(return_value=None)):
            result = self._run(collector, None)
        assert result["exists"] is False
        assert "not_collected" not in result


class TestFindDocDirectory:
    """_find_doc_directory now takes a RepoTree (or COLLECTION_GAP) directly,
    rather than probing paths one at a time -- see corsa-center/metrics#54 and
    METRIC_BLIND_SPOTS.md class F1.
    """

    def test_gap_tree_is_tracked(self, collector):
        path, saw_gap = collector._find_doc_directory(COLLECTION_GAP)
        assert path is None
        assert saw_gap is True

    def test_found_directory_reports_no_gap(self, collector):
        tree = RepoTree(FakeForge(), "o/r", ["docs/index.md"], truncated=False)
        path, saw_gap = collector._find_doc_directory(tree)
        assert path == "docs"
        assert saw_gap is False

    def test_capitalized_docs_directory_is_found(self, collector):
        # AMReX-Codes/amrex ships "Docs" (capital D, lowercase rest); the
        # waiver could never fire for it when case had to be enumerated.
        # Reported in corsa-center/metrics#54.
        tree = RepoTree(FakeForge(), "o/r", ["Docs/index.rst"], truncated=False)
        path, saw_gap = collector._find_doc_directory(tree)
        assert path == "docs"
        assert saw_gap is False

    def test_screaming_case_doc_directory_is_found(self, collector):
        # superlu/superlu_dist/superlu_mt ship DOC/, not doc/ or docs/.
        tree = RepoTree(FakeForge(), "o/r", ["DOC/html/index.html"], truncated=False)
        path, saw_gap = collector._find_doc_directory(tree)
        assert path == "doc"
        assert saw_gap is False

    def test_no_doc_directory_is_a_confirmed_absence(self, collector):
        tree = RepoTree(FakeForge(), "o/r", ["README.md", "src/main.c"], truncated=False)
        path, saw_gap = collector._find_doc_directory(tree)
        assert path is None
        assert saw_gap is False


class TestFindDocumentationSiteGapHandling:
    def _run(self, collector):
        import asyncio
        return asyncio.run(collector._find_documentation_site(None, "o/r"))

    def test_gap_is_tracked_separately_from_confirmed_absence(self, collector):
        from unittest.mock import AsyncMock, patch
        with patch.object(collector.forge, "_github_get", new=AsyncMock(return_value=COLLECTION_GAP)):
            site, saw_gap = self._run(collector)
        assert site is None
        assert saw_gap is True

    def test_confirmed_repo_with_no_homepage_or_pages_is_not_a_gap(self, collector):
        from unittest.mock import AsyncMock, patch
        with patch.object(collector.forge, "_github_get", new=AsyncMock(return_value={"homepage": "", "has_pages": False})):
            site, saw_gap = self._run(collector)
        assert site is None
        assert saw_gap is False


class TestReadmeLinkSections:
    def _sections(self, collector, markdown):
        import asyncio, base64
        from unittest.mock import AsyncMock, patch
        data = {"content": base64.b64encode(markdown.encode()).decode()}
        with patch.object(collector.forge, "_github_get", new=AsyncMock(return_value=data)):
            return asyncio.run(collector._analyze_readme(None, "o/r"))["sections"]

    def test_topics_covered_by_links_count(self, collector):
        # Every topic a linked bullet under one heading.
        md = ("# Pkg\n[Contact Us](https://x/support)\n### Documentation\n"
              "- [Getting Started with Pkg](https://x/getting_started)\n")
        assert set(self._sections(collector, md)) == {"Installation", "Support"}

    def test_link_must_start_with_the_topic(self, collector):
        # An install guide whose title merely contains "using" isn't Usage.
        assert "Usage" not in self._sections(collector, "[Building Pkg using Spack](x)")

    def test_badges_do_not_count(self, collector):
        md = "[![Build and install status](https://x/badge.svg)](https://x/actions)"
        assert self._sections(collector, md) == []


class TestMoreReadmeShapes:
    @pytest.mark.parametrize("text,label", [
        ("### How do I get set up? ###\nbuild it\n", "Installation"),
        ("### Who do I talk to? ###\nemail us\n", "Support"),
        ("## Build TorchBraid: pip (recommended):\npip install .\n", "Installation"),
        ("*************\nInstallation\n*************\n\npip install x\n", "Installation"),
        ("Quick Start\n~~~~~~~~~~~\n\nrun it\n", "Usage"),
        ("MAGMA README\n\n* Quick start (make)\n\n    make\n", "Usage"),
    ])
    def test_heading_found(self, text, label):
        from collectors.quality.usability import readme_covers
        assert readme_covers(text, label)

    def test_markdown_bullets_are_not_headings(self):
        from collectors.quality.usability import readme_covers
        assert not readme_covers("# Tool\n\n* Examples of output\n\nmore\n", "Examples")


class TestDocumentationEvidence:
    """What Documentation Completeness Analysis found and where it looked (#99)."""

    README = ("# Pkg\n\n## Building from source\nmake\n\n"
              "See [Quick start guide](https://x/qs) and [Contact Us](https://x/c).\n")

    def _collect(self, collector, readme, tree_paths, repo_info):
        import asyncio, base64
        from unittest.mock import AsyncMock, patch
        tree = (COLLECTION_GAP if tree_paths is COLLECTION_GAP
                else RepoTree(FakeForge(), "o/r", tree_paths, truncated=False))

        async def get(client, url, params=None):
            if url.endswith("/readme"):
                if readme is COLLECTION_GAP or readme is None:
                    return readme
                return {"content": base64.b64encode(readme.encode()).decode()}
            return repo_info

        with patch.object(RepoTree, "fetch", new=AsyncMock(return_value=tree)), \
             patch.object(collector.forge, "_github_get", side_effect=get):
            r = asyncio.run(collector.collect({"name": "r", "repo_url": "https://github.com/o/r"}))
        return r["overall_score"]["sub_scores"]["documentation_completeness"]["evidence"]

    def test_matched_heading_or_link_is_quoted(self, collector):
        ev = self._collect(collector, self.README, ["src/a.c"], {"homepage": "", "has_pages": False})
        assert ev["found"] == [
            {"text": "Installation (README heading)", "quote": "Building from source"},
            {"text": "Usage (README link)", "quote": "Quick start guide"},
            {"text": "Support (README link)", "quote": "Contact Us"},
        ]

    def test_heading_that_is_the_section_name_is_not_quoted(self, collector):
        ev = self._collect(collector, "# Pkg\n## Usage\nrun\n", ["src/a.c"],
                           {"homepage": "", "has_pages": False})
        assert ev["found"] == [{"text": "Usage (README heading)"}]

    def test_missing_sections_say_what_would_count(self, collector):
        ev = self._collect(collector, self.README, ["src/a.c"], {"homepage": "", "has_pages": False})
        assert ev["missing"] == [
            {"text": "Examples section in the README (a heading or link text: example, demo, sample)"},
            {"text": "documentation directory"},
            {"text": "documentation site (the repository sets no homepage and has no GitHub Pages site)"},
        ]
        assert ev["searched"] == [{"text": "Documentation directories looked for (any capitalization)",
                                   "paths": [{"path": "docs/"}, {"path": "doc/"},
                                             {"path": "documentation/"}]}]

    def test_doc_directory_and_site_are_linked(self, collector):
        ev = self._collect(collector, self.README, ["Docs/index.rst"],
                           {"homepage": "https://pkg.example.org", "has_pages": False})
        assert {"path": "Docs/", "url": "https://github.com/o/r/tree/HEAD/Docs"} in ev["found"]
        assert {"text": "Documentation site (repository homepage)",
                "path": "https://pkg.example.org", "url": "https://pkg.example.org"} in ev["found"]
        assert not ev["searched"]

    def test_absent_readme(self, collector):
        ev = self._collect(collector, None, ["src/a.c"], {"homepage": "", "has_pages": False})
        assert ev["missing"][0] == {"text": "README"}
        assert not any("section in the README" in m["text"] for m in ev["missing"])

    def test_gaps_claim_nothing(self, collector):
        ev = self._collect(collector, COLLECTION_GAP, COLLECTION_GAP, COLLECTION_GAP)
        assert ev == {"found": [], "missing": [], "searched": []}

    def test_every_section_is_described(self):
        from collectors.quality.usability import README_SECTION_TERMS
        assert README_SECTION_TERMS.keys() == _README_SECTIONS.keys()

    def test_readme_match_prefers_a_heading_over_a_link(self):
        from collectors.quality.usability import readme_match
        text = "[Install guide](x)\n\n## Installing\n"
        assert readme_match(text, _README_SECTIONS["Installation"]) == ("heading", "Installing")
        assert readme_match("[Install guide](x)", _README_SECTIONS["Installation"]) == (
            "link", "Install guide")
        assert readme_match("# Pkg\n", _README_SECTIONS["Installation"]) is None

    def test_evidence_reaches_the_report_row(self):
        from orchestrator import MetricsOrchestrator
        from package_report import build_report, render_package_report
        orchestrator = MetricsOrchestrator(config_path="config/orchestrator.yaml")
        collector = UsabilityCollector(GitHubForge())
        readme = {"exists": True, "sections": ["Installation"],
                  "missing": ["Usage", "Examples", "Support"],
                  "matched": {"Installation": {"kind": "heading", "text": "Building <HDF5>"}}}
        tree = RepoTree(FakeForge(), "o/r", ["src/a.c"], truncated=False)
        overall = collector._calculate_score(readme, None, None)
        overall["sub_scores"]["documentation_completeness"]["evidence"] = (
            collector._documentation_evidence(readme, tree, None, None, False, False))
        usability = {"readme": readme, "doc_directory": None, "documentation_site": None,
                     "overall_score": overall}
        metrics = {"overall_score": 0, "last_updated": "2026-11-05T00:00:00+00:00", "dimensions": {
            "impact": {"score": 0.0, "metadata": {"status": "placeholder"}},
            "ecosystem": {"score": 0.0, "sub_results": {}},
            "quality": {"score": 0.0, "sub_results": {"usability": usability}},
        }}
        dashboard = DashboardFormatter().format(metrics, {})
        report = build_report("o/r", dashboard, metrics, orchestrator._metric_weights())
        sec = next(s for d in report["dimensions"] for s in d["sections"] if s["number"] == "4.3.4")
        row = next(r for r in sec["rows"] if r["label"] == "Documentation Completeness Analysis")
        assert row["evidence"]["found"][0]["quote"] == "Building <HDF5>"

        page = render_package_report("o/r", dashboard, metrics, orchestrator._metric_weights())
        assert "<b>Found:</b> Installation (README heading): “Building &lt;HDF5&gt;”" in page
        assert ("<b>Not found:</b> Usage section in the README (a heading or link text: "
                "usage, using, quick start") in page
        assert ("<b>Documentation directories looked for (any capitalization):</b> "
                "docs/, doc/, documentation/") in page


class TestBuildStatusIsNotInstallation:
    @pytest.mark.parametrize("heading", ["Build Status", "Build badges", "Build state"])
    def test_ci_badge_heading_does_not_count(self, heading):
        from collectors.quality.usability import readme_covers
        assert not readme_covers(f"# Project\n\n## {heading}\n\n![ci](x.svg)\n", "Installation")

    @pytest.mark.parametrize("heading", ["Build", "Building", "Build instructions", "Installation"])
    def test_build_instructions_still_count(self, heading):
        from collectors.quality.usability import readme_covers
        assert readme_covers(f"# Project\n\n## {heading}\n\nRun cmake.\n", "Installation")
