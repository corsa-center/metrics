"""The dashboard formatter: each metric as an HTML block of rows.

Metrics are named by path, "<dimension>/<metric>" (ecosystem/licensing).
SECTIONS below is the one list of them: its path, title, builder, and the
CASS report section number when the metric is one of the report's (shown
to readers and used to link to the report; a metric added outside the
report has none).

Builders live beside the collectors they read, one module per dimension
(impact.py, ecosystem.py, quality.py). A builder takes a Context and
returns the metric's HTML, or None when its collectors returned nothing.
Builders read across dimensions where a metric draws on another
dimension's collector (impact/citation_and_adoption on Collaboration,
quality/maintainability on Active Maintenance).
"""

from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

from formatters.base import Formatter
from formatters.dashboard import ecosystem, impact, quality
from formatters.dashboard.common import (
    Context, apply_section_overrides, build_stub_section, rescore_section,
)


@dataclass(frozen=True)
class Section:
    """One metric on the dashboard.

    path: "<dimension>/<metric>"; also where it sits in metrics.json's
        `formatted` block (formatted[dimension][metric]), and the key for
        its thresholds (config/thresholds.yaml) and per-package overrides.
    title: its heading.
    build: its builder, or None for a metric nothing collects yet.
    number: its CASS report section number, or None for a metric that
        isn't in the report.
    submetrics: its sub-metric row labels, for a metric shown as
        "Not yet implemented" rows.
    stub: whether a metric with no collected data shows its sub-metrics as
        "Not yet implemented" rows (with any per-package overrides filled
        in) when the package config overrides any of them.
    """
    path: str
    title: str
    build: Optional[Callable[[Context], Optional[str]]]
    number: Optional[str] = None
    submetrics: Tuple[str, ...] = ()
    stub: bool = False

    @property
    def dimension(self) -> str:
        return self.path.split("/", 1)[0]

    @property
    def key(self) -> str:
        """Its name within the dimension: formatted[dimension][key]."""
        return self.path.split("/", 1)[1]


SECTIONS: Tuple[Section, ...] = (
    # Impact
    Section("impact/citation_and_adoption", "Software Citation and Adoption",
            impact.format_citation_and_adoption, "4.1.1",
            ("Enhanced Citations and Mentions", "Improved DOI Tracking", "Comprehensive Citation Metadata",
             "Advanced Dependency Analysis", "AI-Enhanced Training Detection")),
    Section("impact/field_research", "Field Research Impact", None, "4.1.2",
            ("AI-Enhanced Publication Analysis", "Comprehensive Institutional Tracking",
             "Impact Narrative Extraction"), stub=True),
    # Ecosystem
    Section("ecosystem/governance", "Codes of Conduct (CoC), Governance, and Contributor Guidelines",
            ecosystem.format_governance, "4.2.1",
            ("Enhanced Document Detection", "Governance Keyword Analysis", "OpenSSF Badge Integration",
             "CHAOSS Governance Metrics", "Governance Effectiveness Assessment")),
    Section("ecosystem/licensing", "Open-Source Licensing and FAIR Compliance",
            ecosystem.format_licensing, "4.2.2",
            ("Enhanced License Detection", "Automated FAIR4RS Assessment", "OSI License Validation",
             "License Exception Handling", "FAIR Metadata Assessment")),
    Section("ecosystem/active_maintenance", "Active Maintenance",
            ecosystem.format_active_maintenance, "4.2.3",
            ("Commit Activity Pattern Analysis", "Maintenance Mode Indicator Detection",
             "Activity Trend Monitoring", "Release Pattern Assessment",
             "Multi-Channel Communication Activity", "Contributor Abandonment Forecasting")),
    Section("ecosystem/engagement", "Engagement", ecosystem.format_engagement, "4.2.4",
            ("Response Time Tracking", "Issue Resolution Analysis", "Pull Request Flow Assessment",
             "Support Request Closure Analysis", "Engagement Quality Metrics",
             "Communication Pattern Analysis", "Community Participation Assessment")),
    Section("ecosystem/outreach", "Outreach", ecosystem.format_outreach, "4.2.5",
            ("New Contributor Tracking", "Contributor Retention Analysis", "Contributor Lifecycle Mapping",
             "Contribution Type Diversity", "Good First Issue Effectiveness",
             "External Event Participation", "Training Material Integration",
             "Onboarding Infrastructure Assessment"), stub=True),
    Section("ecosystem/welcomeness", "Welcomeness", ecosystem.format_welcomeness, "4.2.6",
            ("CHAOSS Community Experience Metrics", "Response Quality and Tone Analysis",
             "Communication Sentiment Analysis", "Contributor Journey Mapping",
             "Language and Communication Review", "Leadership Role Representation",
             "Decision-Making Visibility"), stub=True),
    Section("ecosystem/collaboration", "Collaboration", ecosystem.format_collaboration, "4.2.7",
            ("Advanced Dependency Analysis", "Cross-project Reference Detection",
             "Interoperability Assessment", "Collaboration Network Analysis",
             "Standards Compliance Tracking"), stub=True),
    Section("ecosystem/financial_sustainability", "Financial Sustainability",
            ecosystem.format_financial_sustainability, "4.2.8",
            ("Enhanced Funding Documentation Analysis", "Institutional Affiliation Tracking",
             "NIH R50 Award Tracking", "Corporate Sponsorship Detection", "Funding Portfolio Analysis"),
            stub=True),
    Section("ecosystem/institutional_support", "Institutional & Organizational Support",
            ecosystem.format_institutional_support, "4.2.9",
            ("RSE Position Detection", "Institutional Support Tracking", "Career Development Indicators",
             "NIH R50 Award Integration", "Institutional Policy Analysis"), stub=True),
    Section("ecosystem/project_longevity", "Project Longevity and Community Health",
            ecosystem.format_project_longevity, "4.2.10",
            ("Comprehensive Activity Analysis", "Contributor Viability Assessment",
             "Maintenance Mode Detection", "Community Health Trends", "Project Lifecycle Assessment"),
            stub=True),
    # Quality
    Section("quality/reliability", "Reliability and Robustness", quality.format_reliability, "4.3.1",
            ("Advanced Static Analysis", "Enhanced Security Analysis", "CERT Guidelines Compliance",
             "Test Coverage Excellence", "Reliability Trend Analysis")),
    Section("quality/development_practices", "Development Practices",
            quality.format_development_practices, "4.3.2",
            ("CI/CD Effectiveness Assessment", "Testing Framework Excellence", "Code Review Quality Analysis",
             "Development Tool Integration", "Community Contribution Facilitation")),
    Section("quality/reproducibility", "Reproducibility", quality.format_reproducibility, "4.3.3",
            ("FAIR4RS Compliance Assessment", "Containerization Excellence",
             "Version Control Best Practices", "Environment Management", "Reproducibility Documentation")),
    Section("quality/usability", "Usability", quality.format_usability, "4.3.4",
            ("User Experience Assessment", "Documentation Completeness Analysis",
             "Accessibility Feature Detection", "Installation Success Tracking",
             "Usage Analytics Integration"), stub=True),
    Section("quality/accessibility", "Accessibility", quality.format_accessibility, "4.3.5",
            ("Portable Build System Detection", "Container Availability Assessment",
             "Architecture Compatibility Analysis", "Platform Documentation Evaluation",
             "Deployment Environment Testing")),
    Section("quality/maintainability", "Maintainability and Understandability",
            quality.format_maintainability, "4.3.6",
            ("Advanced Complexity Analysis", "Code Quality Assessment", "Documentation Quality Evaluation",
             "Knowledge Distribution Analysis", "Refactoring and Evolution Tracking")),
    Section("quality/performance", "Performance and Efficiency", None, "4.3.7",
            ("Performance Benchmarking Integration", "Environmental Impact Assessment",
             "Resource Utilization Analysis", "Scalability Assessment", "Optimization Practice Evaluation",
             "Memory Efficiency Analysis", "I/O Performance Profiling", "Algorithmic Complexity Assessment",
             "Power Measurement Integration", "Performance Portability Assessment"), stub=True),
    Section("quality/supply_chain", "Software Supply Chain Integrity",
            quality.format_supply_chain, "4.3.8",
            ("SBOM Detection", "Build Provenance", "Dependency Vulnerability Posture", "Dependency Freshness"),
            stub=True),
)

SECTIONS_BY_PATH: Dict[str, Section] = {section.path: section for section in SECTIONS}
DIMENSIONS: Tuple[str, ...] = ("impact", "ecosystem", "quality")


class DashboardFormatter(Formatter):
    """Per-package metrics for the dashboard.

    The result is keyed by dimension and then by metric
    (result["ecosystem"]["licensing"] is ecosystem/licensing), each with a
    ``title``, ``path``, ``number`` (its CASS report section, or None) and
    ``data`` (an HTML string or None), plus ``config_exclusions``.
    `overrides` is keyed by metric path.
    """

    def format(self, metrics: Dict, overrides: Dict[str, Dict[str, str]]) -> Dict:
        dims = metrics.get("dimensions", {})
        ctx = Context(dims)
        result: Dict = {
            # Sub-collector keys turned off by package_config/ or the
            # project's own file (not by the operator's global config) --
            # lets the dashboard show "excluded by project" rather than
            # leaving a reader to guess whether a blank section was skipped
            # or simply crashed.
            "config_exclusions": {
                "ecosystem": dims.get("ecosystem", {}).get("excluded_by_config", []),
                "quality": dims.get("quality", {}).get("excluded_by_config", []),
            },
        }
        for dim in DIMENSIONS:
            result[dim] = {}
        for section in SECTIONS:
            section_overrides = overrides.get(section.path, {})
            data = None
            if section.build is not None:
                data = apply_section_overrides(section.build(ctx), section_overrides)
            if not data and section.stub:
                if section.path in overrides:
                    data = build_stub_section(section.submetrics, section_overrides)
                else:
                    data = None
            result[section.dimension][section.key] = {
                "title": section.title,
                "path": section.path,
                "number": section.number,
                # One scoring rule for every section, instead of each
                # builder's own denominator -- see rescore_section.
                "data": rescore_section(data),
            }
        return result
