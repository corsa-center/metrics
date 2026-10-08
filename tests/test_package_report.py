"""Tests for the per-package metric report (package_report.py, issue #80)."""

import asyncio

import pytest

from collectors.ecosystem.base import configure_threshold_overrides
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
        dashboard = orchestrator._transform_for_dashboard(metrics, {})
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
        dashboard = orchestrator._transform_for_dashboard(metrics, overrides)
        report = build_report("owner/repo", dashboard, metrics, orchestrator._metric_weights(), overrides)
        rows = next(s for d in report["dimensions"] for s in d["sections"] if s["number"] == "4.2.8")["rows"]
        flagged = {r["label"] for r in rows if r["overridden"]}
        assert flagged == {"NIH R50 Award Tracking"}


class TestHtmlOutput:
    def test_report_written_beside_metrics_json(self, orchestrator, tmp_path):
        orchestrator.output_path = tmp_path
        metrics = _metrics(
            excluded_by_config=["funding"],
            score_components={"governance": 66.7, "licensing": 100},
        )
        orchestrator._write_dashboard_output({"repo": {}}, {"repo": metrics})
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
        dashboard = orchestrator._transform_for_dashboard(metrics, {})
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
            orchestrator, "_sub_enabled",
            lambda group, key, package=None: key in ("community_health", "licensing"),
        )
        result = asyncio.run(orchestrator.collect_ecosystem_dimension(
            {"name": "x", "repo_url": "https://github.com/o/r", "repository": "o/r"}
        ))
        assert result["score_components"] == {"governance": 50, "licensing": 100}
        assert result["score"] == 75.0
