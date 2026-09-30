"""Tests for the per-project metric report (project_report.py, issue #80)."""

import pytest

from collectors.ecosystem.base import configure_threshold_overrides
from orchestrator import MetricsOrchestrator
from project_report import (
    render_project_report,
    section_lines,
    threshold_descriptions,
    threshold_lines,
)


@pytest.fixture
def orchestrator():
    return MetricsOrchestrator(config_path="config/orchestrator.yaml")


@pytest.fixture
def reset_overrides():
    yield
    configure_threshold_overrides(None)


class TestSectionLines:
    def test_rows_details_and_links(self):
        html = (
            '<p><strong>Enhanced Security Analysis:</strong> '
            '<a href="https://x/codeql.yml">CodeQL enabled</a> ✓</p>\n'
            '<p class="sub-detail">Practice indicators, not audited</p>'
        )
        assert section_lines(html) == [
            "- **Enhanced Security Analysis:** [CodeQL enabled](https://x/codeql.yml) ✓",
            "  - Practice indicators, not audited",
        ]

    def test_detail_on_the_same_line_as_its_row(self):
        # 4.3.3 and 4.3.5 emit a row and its detail without a newline between.
        html = '<p><strong>Containerization Excellence:</strong> ✓</p><p class="sub-detail">Dockerfile</p>'
        assert section_lines(html) == [
            "- **Containerization Excellence:** ✓",
            "  - Dockerfile",
        ]

    def test_entities_are_unescaped(self):
        html = '<p><strong>License:</strong> Apache &amp; MIT</p>'
        assert section_lines(html) == ["- **License:** Apache & MIT"]

    def test_uncollected_section(self):
        assert section_lines(None) == ["- Not yet collected"]


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
        lines = threshold_lines("4.2.2", threshold_descriptions())
        assert any(l.startswith("- FAIR Metadata Assessment: 5 ") for l in lines)

    def test_section_without_thresholds(self):
        assert threshold_lines("4.3.7", threshold_descriptions()) == []


class TestReportOutput:
    def test_report_written_beside_metrics_json(self, orchestrator, tmp_path):
        orchestrator.output_path = tmp_path
        metrics = {
            "overall_score": 27,
            "last_updated": "2026-09-30T00:00:00+00:00",
            "dimensions": {
                "impact": {"score": 0.0, "sub_results": {}},
                "ecosystem": {"score": 80.0, "sub_results": {}, "excluded_by_config": ["funding"]},
                "quality": {"score": 0.0, "sub_results": {}},
            },
        }
        orchestrator._write_dashboard_output({"owner/repo": metrics})
        report = (tmp_path / "repo-metrics" / "report.md").read_text()

        assert report.startswith("# Sustainability Metrics Report: owner/repo\n")
        assert "Overall score: 27/100" in report
        assert "Ecosystem 80 × 0.34" in report
        assert "turned off by this project's configuration: ecosystem/funding" in report
        assert "## Ecosystem Dimension — 80/100" in report
        assert "### 4.2.2 Open-Source Licensing and FAIR Compliance" in report
        # Thresholds listed even when the section itself wasn't collected.
        assert "- FAIR Metadata Assessment: 4 (of 6 CITATION.cff fields present, minimum)" in report

    def test_report_matches_dashboard_rows(self, orchestrator):
        metrics = {"dimensions": {"ecosystem": {"sub_results": {
            "governance": {"overall_score": {"max_score": 3}},
        }}}}
        dashboard = orchestrator._transform_for_dashboard("owner/repo", metrics)
        report = render_project_report(dashboard, metrics, orchestrator._metric_weights())
        assert "- **Enhanced Document Detection:** 0/3 ✗" in report
        assert "  - Code of Conduct: Not found" in report
