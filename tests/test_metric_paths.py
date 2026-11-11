"""Metrics are named by path, "<dimension>/<metric>" (#107): the one list of
them is formatters/dashboard SECTIONS, and thresholds, per-package
overrides, metrics.json's `formatted` block and report.html all key by it.
The CASS report section number is only for display and links to the report.
"""

import re
from pathlib import Path

import yaml

from formatters.dashboard import DIMENSIONS, SECTIONS, DashboardFormatter, Section
from package_report import build_report, render_html

THRESHOLDS_YAML = Path(__file__).resolve().parent.parent / "config" / "thresholds.yaml"
_PATH = re.compile(r"^(impact|ecosystem|quality)/[a-z][a-z0-9_]*$")


class TestRegistry:
    def test_paths_are_well_formed_and_unique(self):
        paths = [s.path for s in SECTIONS]
        assert len(set(paths)) == len(paths)
        assert all(_PATH.match(p) for p in paths), [p for p in paths if not _PATH.match(p)]

    def test_report_numbers_are_unique(self):
        numbers = [s.number for s in SECTIONS if s.number]
        assert len(set(numbers)) == len(numbers)

    def test_every_dimension_has_metrics(self):
        assert {s.dimension for s in SECTIONS} == set(DIMENSIONS)

    def test_threshold_keys_are_metric_paths(self):
        keys = set(yaml.safe_load(THRESHOLDS_YAML.read_text()))
        assert keys <= {s.path for s in SECTIONS}, keys - {s.path for s in SECTIONS}


class TestFormatted:
    def test_nested_by_path_with_path_and_number(self):
        out = DashboardFormatter().format({"dimensions": {}}, {})
        entry = out["ecosystem"]["licensing"]
        assert entry["path"] == "ecosystem/licensing"
        assert entry["number"] == "4.2.2"
        assert [k for k in out if k != "config_exclusions"] == list(DIMENSIONS)

    def test_overrides_are_keyed_by_path(self):
        overrides = {"ecosystem/financial_sustainability": {"NIH R50 Award Tracking": "N/A"}}
        out = DashboardFormatter().format({"dimensions": {}}, overrides)
        assert "<strong>NIH R50 Award Tracking:</strong> N/A" in out["ecosystem"]["financial_sustainability"]["data"]


class TestMetricOutsideTheReport:
    """A metric that isn't in the CASS report has no number: it is shown by
    title, anchored by path, and has no methodology link."""

    def test_rendered_by_title_and_anchored_by_path(self, monkeypatch):
        import formatters.dashboard as dashboard
        extra = Section("quality/energy_use", "Energy Use", lambda ctx: "<p><strong>Watts:</strong> 3 ✓</p>")
        monkeypatch.setattr(dashboard, "SECTIONS", dashboard.SECTIONS + (extra,))
        metrics = {"dimensions": {}, "last_updated": "2026-11-11T00:00:00+00:00"}
        formatted = DashboardFormatter().format(metrics, {})
        assert formatted["quality"]["energy_use"]["number"] is None

        report = build_report("o/r", formatted, metrics, {"impact": 0.33, "ecosystem": 0.34, "quality": 0.33})
        sec = next(s for d in report["dimensions"] for s in d["sections"] if s["path"] == "quality/energy_use")
        assert sec["methodology"] is None
        page = render_html(report)
        assert '<h3 id="quality-energy_use">Energy Use' in page
        assert '<a href="#quality-energy_use">Energy Use</a>' in page
