"""Tests for the per-package metric report (package_report.py, issue #80)."""

import asyncio
import json

import pytest
from formatters.dashboard import DashboardFormatter

from collectors.common import configure_threshold_overrides
from orchestrator import MetricsOrchestrator
from package_report import (
    build_report,
    parse_section,
    render_package_report,
    section_thresholds,
    threshold_descriptions,
)


@pytest.fixture
def orchestrator():
    return MetricsOrchestrator(config_path="config/orchestrator.yaml")


@pytest.fixture
def reset_overrides():
    yield
    configure_threshold_overrides(None)


def _metrics(**ecosystem):
    return {
        "overall_score": 27,
        "last_updated": "2026-09-30T02:25:04.582663+00:00",
        "dimensions": {
            "impact": {"score": 0.0, "metadata": {"status": "placeholder"}},
            "ecosystem": {"score": 80.0, "sub_results": {}, **ecosystem},
            "quality": {"score": 0.0, "sub_results": {}},
        },
    }


class TestParseSection:
    def test_rows_marks_details_and_links(self):
        html = (
            '<p><strong>Enhanced Security Analysis:</strong> '
            '<a href="https://x/codeql.yml">CodeQL enabled</a> ✓</p>\n'
            '<p class="sub-detail">Practice indicators, not audited</p>\n'
            '<p><strong>Score:</strong> 1/1</p>'
        )
        rows, score = parse_section(html)
        assert score == "1/1"
        assert rows == [{
            "label": "Enhanced Security Analysis",
            "value": [("CodeQL enabled", "https://x/codeql.yml")],
            "mark": "✓",
            "details": [[("Practice indicators, not audited", None)]],
        }]

    def test_detail_on_the_same_line_as_its_row(self):
        # 4.3.3 and 4.3.5 emit a row and its detail without a newline between.
        html = '<p><strong>Containerization Excellence:</strong> ✓</p><p class="sub-detail">Dockerfile</p>'
        rows, _ = parse_section(html)
        assert rows[0]["value"] == [] and rows[0]["mark"] == "✓"
        assert rows[0]["details"] == [[("Dockerfile", None)]]

    def test_unmarked_row_and_entities(self):
        rows, score = parse_section('<p><strong>License:</strong> Apache &amp; MIT</p>')
        assert score is None
        assert rows[0]["mark"] is None
        assert rows[0]["value"] == [("Apache & MIT", None)]


class TestThresholds:
    def test_inline_comment_and_its_continuation(self):
        desc = threshold_descriptions()
        assert desc[("4.2.2", "Automated FAIR4RS Assessment")] == (
            "of 4 FAIR principles satisfied, minimum"
        )
        # Wrapped onto a second comment line in thresholds.yaml.
        assert desc[("4.2.3", "Multi-Channel Communication Activity", "min_community_issues")].endswith(
            "before the issue tracker counts as a channel"
        )

    def test_block_rationale_is_not_a_description(self):
        # 4.2.7's params carry only a block comment above them, no inline one.
        desc = threshold_descriptions()
        assert ("4.2.7", "Collaboration Network Analysis", "min_dependent_packages") not in desc

    def test_overrides_are_what_gets_reported(self, reset_overrides):
        configure_threshold_overrides({"4.2.2": {"FAIR Metadata Assessment": 5}})
        lines = section_thresholds("4.2.2", threshold_descriptions())
        assert lines["FAIR Metadata Assessment"][0].startswith("5 — ")

    def test_keyed_by_the_label_the_dashboard_row_carries(self):
        assert "OpenSSF Scorecard" in section_thresholds("4.2.1", threshold_descriptions())


class TestBuildReport:
    def test_thresholds_attach_to_their_rows(self, orchestrator):
        metrics = _metrics(sub_results={"governance": {"overall_score": {"max_score": 3}}})
        dashboard = DashboardFormatter().format(metrics, {})
        report = build_report("owner/repo", dashboard, metrics, orchestrator._metric_weights())
        sec = report["dimensions"][1]["sections"][0]
        assert sec["number"] == "4.2.1"
        row = next(r for r in sec["rows"] if r["label"] == "Enhanced Document Detection")
        assert row["mark"] == "✗"
        assert row["thresholds"] == ["2 — of {CoC, Governance, Contributing}, minimum found"]
        # No Scorecard row rendered for this package, so its threshold is
        # listed separately rather than dropped.
        assert "OpenSSF Scorecard" in sec["other_thresholds"]

    def test_config_overridden_rows_are_flagged(self, orchestrator):
        metrics = _metrics()
        overrides = {"4.2.8": {"NIH R50 Award Tracking": "N/A"}}
        dashboard = DashboardFormatter().format(metrics, overrides)
        report = build_report("owner/repo", dashboard, metrics, orchestrator._metric_weights(), overrides)
        rows = next(s for d in report["dimensions"] for s in d["sections"] if s["number"] == "4.2.8")["rows"]
        flagged = {r["label"] for r in rows if r["overridden"]}
        assert flagged == {"NIH R50 Award Tracking"}


class TestHtmlOutput:
    def test_metrics_json_keeps_the_dashboards_flat_layout(self, orchestrator, tmp_path):
        # js/catalog.js reads these from the top level.
        catalog = {"HDF5": {"name": "HDF5", "repo_url": "https://github.com/HDFGroup/hdf5"}}
        orchestrator.write_metrics(tmp_path, catalog, {"HDF5": _metrics()})
        data = json.loads((tmp_path / "hdf5-metrics" / "metrics.json").read_text())
        for key in ("config_exclusions", "impact", "ecosystem", "quality"):
            assert key in data
        # Repository counts live in metadata only.
        assert "stars" not in data and "forks" not in data
        assert data["package"] == "HDF5"
        assert data["metadata"] == catalog["HDF5"]
        assert data["generated_at"] == "2026-09-30T02:25:04.582663+00:00"

    def test_report_written_beside_metrics_json(self, orchestrator, tmp_path):
        metrics = _metrics(
            excluded_by_config=["funding"],
            score_components={"governance": 66.7, "licensing": 100},
        )
        orchestrator.write_metrics(tmp_path, {"repo": {}}, {"repo": metrics})
        page = (tmp_path / "repo-metrics" / "report.html").read_text()

        assert page.startswith("<!doctype html>")
        assert "Sustainability metrics report: repo" in page
        assert "Collected 2026-09-30 02:25 UTC" in page
        assert "Governance documents (4.2.1) 67; Licensing (4.2.2) 100" in page
        assert "Not yet measured — counted as 0 in the overall score" in page
        assert "not collected: Financial sustainability (4.2.8)" in page
        assert 'id="s4-2-2"' in page

    def test_collected_text_is_escaped(self, orchestrator):
        metrics = _metrics(sub_results={"governance": {
            "overall_score": {"max_score": 3},
            "keyword_analysis": {"groups_found": ["<script>alert(1)</script>"]},
        }})
        dashboard = DashboardFormatter().format(metrics, {})
        page = render_package_report("owner/repo", dashboard, metrics, orchestrator._metric_weights())
        # The dashboard HTML carries it raw; the report reduces it to text.
        assert "<script>" not in page
        assert "alert(1)" in page


class TestDimensionScoreComponents:
    def test_components_are_what_the_score_averages(self, orchestrator, monkeypatch):
        import collectors.ecosystem.community_health as community_health_mod
        import collectors.ecosystem.licensing as licensing_mod

        async def gov(self, package):
            return {"overall_score": {"percentage": 50}}

        async def lic(self, package):
            return {"compliance_score": {"percentage": 100}}

        monkeypatch.setattr(community_health_mod.CommunityHealthCollector, "collect", gov)
        monkeypatch.setattr(licensing_mod.LicensingCollector, "collect", lic)
        monkeypatch.setattr(
            orchestrator.dimensions["ecosystem"], "sub_enabled",
            lambda key, package=None: key in ("community_health", "licensing"),
        )
        result = asyncio.run(orchestrator.dimensions["ecosystem"].collect(
            {"name": "x", "repo_url": "https://github.com/o/r", "repository": "o/r"}
        ))
        assert result["score_components"] == {"governance": 50, "licensing": 100}
        assert result["score"] == 75.0


class TestMethodologyLinks:
    def test_every_section_links_to_its_catalog_heading(self, orchestrator):
        from package_report import METRICS_CATALOG_URL
        metrics = _metrics()
        dashboard = DashboardFormatter().format(metrics, {})
        report = build_report("owner/repo", dashboard, metrics, orchestrator._metric_weights())
        links = {s["number"]: s["methodology"] for d in report["dimensions"] for s in d["sections"]}
        assert all(links.values()), [n for n, url in links.items() if not url]
        assert links["4.2.8"] == f"{METRICS_CATALOG_URL}#428-financial-sustainability"
        # GitHub drops the "&" and keeps both hyphens around it.
        assert links["4.2.9"].endswith("#429-institutional--organizational-support")

    def test_first_heading_for_a_number_wins(self, tmp_path):
        from package_report import methodology_links
        path = tmp_path / "catalog.md"
        path.write_text("### 4.1.1 Software Citation\n\n### 4.1.1 / 4.2.7 additions\n")
        assert methodology_links(path)["4.1.1"].endswith("#411-software-citation")

    def test_missing_catalog_means_no_links(self, tmp_path):
        from package_report import methodology_links
        assert methodology_links(tmp_path / "absent.md") == {}


def _funding_metrics(**doc_entry):
    entry = {
        "label": "Enhanced Funding Documentation Analysis",
        "value": "No funding documentation found", "passing": False,
        "evidence": {
            "found": [{"text": "<b>DOE</b> named as a funder", "quote": "funded by <i>DOE</i>",
                       "path": "NOTICE", "url": "https://github.com/o/r/blob/HEAD/NOTICE"}],
            "searched": [
                {"text": "Files searched for award numbers and funder acknowledgments",
                 "paths": [{"path": "README.md", "url": "javascript:alert(1)"}]},
                {"text": "Funding files looked for", "paths": []},
            ],
        },
        **doc_entry,
    }
    return _metrics(sub_results={"funding": {"overall_score": {
        "score": 0, "max_score": 1, "sub_scores": {"funding_documentation": entry},
    }}})


class TestEvidence:
    def _row(self, orchestrator, metrics, overrides=None):
        dashboard = DashboardFormatter().format(metrics, overrides or {})
        report = build_report("owner/repo", dashboard, metrics,
                              orchestrator._metric_weights(), overrides)
        sec = next(s for d in report["dimensions"] for s in d["sections"] if s["number"] == "4.2.8")
        return next(r for r in sec["rows"] if r["label"] == "Enhanced Funding Documentation Analysis")

    def test_attached_to_the_row_it_was_recorded_for(self, orchestrator):
        metrics = _funding_metrics()
        row = self._row(orchestrator, metrics)
        assert row["evidence"]["found"][0]["path"] == "NOTICE"

    def test_rendered_escaped_with_links_and_where_it_looked(self, orchestrator):
        metrics = _funding_metrics()
        dashboard = DashboardFormatter().format(metrics, {})
        page = render_package_report("owner/repo", dashboard, metrics, orchestrator._metric_weights())
        assert ('<b>Found:</b> &lt;b&gt;DOE&lt;/b&gt; named as a funder in '
                '<a href="https://github.com/o/r/blob/HEAD/NOTICE">NOTICE</a>: '
                '“funded by &lt;i&gt;DOE&lt;/i&gt;”') in page
        # Only web URLs become links.
        assert ("<b>Files searched for award numbers and funder acknowledgments:</b> README.md"
                in page)
        assert "javascript:" not in page
        assert "<b>Funding files looked for:</b> none present" in page

    def test_config_overridden_row_shows_no_evidence(self, orchestrator):
        overrides = {"4.2.8": {"Enhanced Funding Documentation Analysis": "See our NOTICE"}}
        row = self._row(orchestrator, _funding_metrics(), overrides)
        assert row["overridden"] and row["evidence"] is None

    def test_labels_are_matched_within_their_own_dimension(self):
        from package_report import evidence_index
        # "Advanced Dependency Analysis" is a row in both 4.1.1 and 4.2.7.
        impact = {"citation": {"x": {"label": "Advanced Dependency Analysis",
                                     "evidence": {"found": [{"text": "impact"}]}}}}
        eco = {"collaboration": {"sub_scores": {"deps": {"label": "Advanced Dependency Analysis",
                                                         "evidence": {"found": [{"text": "eco"}]}}}}}
        assert evidence_index(impact)["Advanced Dependency Analysis"]["found"][0]["text"] == "impact"
        assert evidence_index(eco)["Advanced Dependency Analysis"]["found"][0]["text"] == "eco"
        assert evidence_index(None) == {}


class TestGovernanceEvidence:
    def test_row_evidence_reaches_the_four_two_one_rows(self, orchestrator):
        from collectors.ecosystem.community_health import CommunityHealthCollector
        c = CommunityHealthCollector(None)
        missing = {"exists": False, "looked_in_repos": ["o/r", "o/governance"]}
        found = {"exists": True, "file_path": "CODE_OF_CONDUCT.md", "url": "https://x/coc",
                 "repository": "o/r"}
        keywords = {"terms_found": {"Decision process": ["<vote>"]}, "documents": []}
        governance = {
            "code_of_conduct": found, "governance": missing, "contributing_guidelines": found,
            "keyword_analysis": {"groups_found": ["Decision process"], **keywords},
            "effectiveness": {}, "overall_score": {"max_score": 3},
            "row_evidence": c._row_evidence("o/r", found, missing, found, keywords, {}),
        }
        metrics = _metrics(sub_results={"governance": governance})
        dashboard = DashboardFormatter().format(metrics, {})
        page = render_package_report("o/r", dashboard, metrics, orchestrator._metric_weights())

        assert "<b>Not found:</b> Governance</div>" in page
        assert "<b>Governance also looked for in:</b> the o/governance repository" in page
        assert "<b>Found:</b> Decision process (matched: &lt;vote&gt;)" in page
        assert "<b>Not found:</b> Membership lifecycle (looked for: nomination" in page


class TestCrossSectionEvidence:
    """Installation Success Tracking renders in 4.3.4 (Quality) but comes
    from the Ecosystem collaboration collector."""

    def _metrics(self, installation_success):
        metrics = _metrics(sub_results={"collaboration": {"overall_score": {
            "score": 0, "max_score": 0, "sub_scores": {"installation_success": installation_success},
        }}})
        # 4.3.4 renders only alongside a usability result.
        metrics["dimensions"]["quality"]["sub_results"]["usability"] = {
            "overall_score": {"sub_scores": {}}}
        return metrics

    def test_evidence_reaches_the_row_in_the_other_dimension(self, orchestrator):
        from collectors.ecosystem.collaboration import CollaborationCollector
        registries = [{"name": "h5py", "ecosystem": "pypi", "registry_url": "https://pypi.org/project/h5py",
                       "install_command": "pip install h5py"}]
        entry = {"label": "Installation Success Tracking", "value": "Installable from 1 package manager: pypi",
                 "passing": True, "section": "4.3.4",
                 "evidence": CollaborationCollector._install_evidence(registries, {})}
        metrics = self._metrics(entry)
        dashboard = DashboardFormatter().format(metrics, {})
        report = build_report("o/r", dashboard, metrics, orchestrator._metric_weights())
        rows = {(s["number"], r["label"]): r for d in report["dimensions"]
                for s in d["sections"] for r in s["rows"]}
        row = rows[("4.3.4", "Installation Success Tracking")]
        assert row["evidence"]["found"] == [{"path": "h5py (pypi)", "url": "https://pypi.org/project/h5py",
                                             "quote": "pip install h5py"}]
        page = render_package_report("o/r", dashboard, metrics, orchestrator._metric_weights())
        assert ('<b>Found:</b> <a href="https://pypi.org/project/h5py">h5py (pypi)</a>: '
                '“pip install h5py”') in page

    def test_sectioned_entries_stay_out_of_the_label_index(self):
        from package_report import evidence_index, sectioned_evidence
        entry = {"label": "Installation Success Tracking", "section": "4.3.4", "evidence": {"found": []}}
        dims = {"ecosystem": {"sub_results": {"collaboration": {"x": entry}}}}
        assert evidence_index(dims["ecosystem"]["sub_results"]) == {}
        assert sectioned_evidence(dims) == {("4.3.4", "Installation Success Tracking"): {"found": []}}

    def test_no_install_path_names_the_packages_and_the_search(self):
        from collectors.ecosystem.collaboration import CollaborationCollector
        registries = [{"name": "r", "ecosystem": "spack", "registry_url": None}]
        lookups = {"repository": {"url": "https://github.com/o/r", "failed": False}}
        ev = CollaborationCollector._install_evidence(registries, lookups)
        assert ev["missing"] == [{"text": "an install command for r (spack)"}]
        assert ev["searched"][0]["paths"] == [{"path": "https://github.com/o/r"}]


class TestUnlessMet:
    def test_item_is_dropped_from_a_met_row_only(self):
        from package_report import _render_evidence
        evidence = {"missing": [{"text": "an SPDX id", "unless_met": True}, {"text": "other"}]}
        met = "".join(_render_evidence(evidence, met=True))
        unmet = "".join(_render_evidence(evidence, met=False))
        assert "an SPDX id" not in met and "other" in met
        assert "an SPDX id" in unmet


class TestRepeatedDetailLines:
    def _details(self, details, found, mark="✓"):
        from package_report import _uncovered_details
        row = {"details": [[(d, None)] for d in details], "mark": mark,
               "evidence": {"found": found}}
        return ["".join(t for t, _ in d) for d in _uncovered_details(row)]

    def test_detail_fully_covered_by_found_lines_is_dropped(self):
        found = [{"text": "Installation (README heading)"}, {"text": "Usage (README heading)"},
                 {"text": "Support (README heading)"}]
        assert self._details(["Installation, Usage, Support"], found) == []

    def test_a_path_counts_as_covering(self):
        assert self._details(["CITATION.cff"], [{"path": "CITATION.cff", "url": "u"}]) == []

    def test_partly_covered_detail_is_kept(self):
        found = [{"text": "Forum", "path": "README.md"}]
        assert self._details(["GitHub Discussions, Wiki, Forum"], found) == [
            "GitHub Discussions, Wiki, Forum"]

    def test_no_evidence_keeps_every_detail(self):
        from package_report import _uncovered_details
        row = {"details": [[("x", None)]], "mark": "✓", "evidence": None}
        assert _uncovered_details(row) == row["details"]

    def test_funding_award_number_appears_once(self, orchestrator):
        entry = {"label": "Enhanced Funding Documentation Analysis",
                 "value": "1 award reference(s)", "passing": True, "detail": "DE-AC05-00OR22725",
                 "evidence": {"found": [{"text": "DE-AC05-00OR22725 (DOE contract)",
                                         "path": "README.md", "url": None}]}}
        metrics = _metrics(sub_results={"funding": {"overall_score": {
            "score": 1, "max_score": 1, "sub_scores": {"funding_documentation": entry}}}})
        dashboard = DashboardFormatter().format(metrics, {})
        page = render_package_report("o/r", dashboard, metrics, orchestrator._metric_weights())
        assert page.count("DE-AC05-00OR22725") == 1
        # The dashboard keeps its own detail line.
        assert "DE-AC05-00OR22725</p>" in dashboard["ecosystem"]["4.2.8"]["data"]
