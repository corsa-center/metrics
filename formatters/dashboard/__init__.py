"""The dashboard formatter: each CASS report section as an HTML block.

Section builders live beside the collectors they read, one module per
dimension (impact.py, ecosystem.py, quality.py). A builder takes a Context
and returns the section's HTML, or None when its collectors returned
nothing. Builders read across dimensions where a section draws on another
dimension's collector (4.1.1 on Collaboration, 4.3.6 on Active Maintenance).
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
    """One section of the dashboard.

    build: its builder, or None for a section nothing collects yet.
    stub: whether a section with no collected data shows its sub-metrics
        as "Not yet implemented" (with any per-package overrides filled in)
        when the package config overrides any of them.
    """
    number: str
    title: str
    build: Optional[Callable[[Context], Optional[str]]]
    stub: bool = False


SECTIONS: Dict[str, Tuple[Section, ...]] = {
    "impact": (
        Section("4.1.1", "Software Citation and Adoption", impact.section_4_1_1),
        Section("4.1.2", "Field Research Impact", None, stub=True),
    ),
    "ecosystem": (
        Section("4.2.1", "Codes of Conduct (CoC), Governance, and Contributor Guidelines",
                ecosystem.section_4_2_1),
        Section("4.2.2", "Open-Source Licensing and FAIR Compliance", ecosystem.section_4_2_2),
        Section("4.2.3", "Active Maintenance", ecosystem.section_4_2_3),
        Section("4.2.4", "Engagement", ecosystem.section_4_2_4),
        Section("4.2.5", "Outreach", ecosystem.section_4_2_5, stub=True),
        Section("4.2.6", "Welcomeness", ecosystem.section_4_2_6, stub=True),
        Section("4.2.7", "Collaboration", ecosystem.section_4_2_7, stub=True),
        Section("4.2.8", "Financial Sustainability", ecosystem.section_4_2_8, stub=True),
        Section("4.2.9", "Institutional & Organizational Support", ecosystem.section_4_2_9, stub=True),
        Section("4.2.10", "Project Longevity and Community Health", ecosystem.section_4_2_10, stub=True),
    ),
    "quality": (
        Section("4.3.1", "Reliability and Robustness", quality.section_4_3_1),
        Section("4.3.2", "Development Practices", quality.section_4_3_2),
        Section("4.3.3", "Reproducibility", quality.section_4_3_3),
        Section("4.3.4", "Usability", quality.section_4_3_4, stub=True),
        Section("4.3.5", "Accessibility", quality.section_4_3_5),
        Section("4.3.6", "Maintainability and Understandability", quality.section_4_3_6),
        Section("4.3.7", "Performance and Efficiency", None, stub=True),
        Section("4.3.8", "Software Supply Chain Integrity", quality.section_4_3_8, stub=True),
    ),
}


class DashboardFormatter(Formatter):
    """Per-package CASS v3 sections for the dashboard.

    The result is keyed by dimension and then by CASS report section
    number (4.1.x, 4.2.x, 4.3.x), each with a ``title`` and ``data`` (an
    HTML string or None), plus ``config_exclusions``.
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
        for dim, sections in SECTIONS.items():
            result[dim] = {}
            for section in sections:
                data = None
                if section.build is not None:
                    data = apply_section_overrides(section.build(ctx), overrides.get(section.number, {}))
                if not data and section.stub:
                    data = (build_stub_section(section.number, overrides[section.number])
                            if section.number in overrides else None)
                # One scoring rule for every section, instead of each
                # builder's own denominator -- see rescore_section.
                result[dim][section.number] = {"title": section.title, "data": rescore_section(data)}
        return result
