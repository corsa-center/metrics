"""Shared pieces of the dashboard formatter: the context every section
builder reads, the row helpers, per-package overrides, stub sections for
sub-metrics nothing collects yet, and the one scoring rule applied to
every section."""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# Sub-metric labels per section (CASS Sustainability Metrics Report v3)
SECTION_SUBMETRICS: Dict[str, List[str]] = {
    "4.1.1": ["Enhanced Citations and Mentions", "Improved DOI Tracking", "Comprehensive Citation Metadata", "Advanced Dependency Analysis", "AI-Enhanced Training Detection"],
    "4.1.2": ["AI-Enhanced Publication Analysis", "Comprehensive Institutional Tracking", "Impact Narrative Extraction"],
    "4.2.1": ["Enhanced Document Detection", "Governance Keyword Analysis", "OpenSSF Badge Integration", "CHAOSS Governance Metrics", "Governance Effectiveness Assessment"],
    "4.2.2": ["Enhanced License Detection", "Automated FAIR4RS Assessment", "OSI License Validation", "License Exception Handling", "FAIR Metadata Assessment"],
    "4.2.3": ["Commit Activity Pattern Analysis", "Maintenance Mode Indicator Detection", "Activity Trend Monitoring", "Release Pattern Assessment", "Multi-Channel Communication Activity", "Contributor Abandonment Forecasting"],
    "4.2.4": ["Response Time Tracking", "Issue Resolution Analysis", "Pull Request Flow Assessment", "Support Request Closure Analysis", "Engagement Quality Metrics", "Communication Pattern Analysis", "Community Participation Assessment"],
    "4.2.5": ["New Contributor Tracking", "Contributor Retention Analysis", "Contributor Lifecycle Mapping", "Contribution Type Diversity", "Good First Issue Effectiveness", "External Event Participation", "Training Material Integration", "Onboarding Infrastructure Assessment"],
    "4.2.6": ["CHAOSS Community Experience Metrics", "Response Quality and Tone Analysis", "Communication Sentiment Analysis", "Contributor Journey Mapping", "Language and Communication Review", "Leadership Role Representation", "Decision-Making Visibility"],
    "4.2.7": ["Advanced Dependency Analysis", "Cross-project Reference Detection", "Interoperability Assessment", "Collaboration Network Analysis", "Standards Compliance Tracking"],
    "4.2.8": ["Enhanced Funding Documentation Analysis", "Institutional Affiliation Tracking", "NIH R50 Award Tracking", "Corporate Sponsorship Detection", "Funding Portfolio Analysis"],
    "4.2.9": ["RSE Position Detection", "Institutional Support Tracking", "Career Development Indicators", "NIH R50 Award Integration", "Institutional Policy Analysis"],
    "4.2.10": ["Comprehensive Activity Analysis", "Contributor Viability Assessment", "Maintenance Mode Detection", "Community Health Trends", "Project Lifecycle Assessment"],
    "4.3.1": ["Advanced Static Analysis", "Enhanced Security Analysis", "CERT Guidelines Compliance", "Test Coverage Excellence", "Reliability Trend Analysis"],
    "4.3.2": ["CI/CD Effectiveness Assessment", "Testing Framework Excellence", "Code Review Quality Analysis", "Development Tool Integration", "Community Contribution Facilitation"],
    "4.3.3": ["FAIR4RS Compliance Assessment", "Containerization Excellence", "Version Control Best Practices", "Environment Management", "Reproducibility Documentation"],
    "4.3.4": ["User Experience Assessment", "Documentation Completeness Analysis", "Accessibility Feature Detection", "Installation Success Tracking", "Usage Analytics Integration"],
    "4.3.5": ["Portable Build System Detection", "Container Availability Assessment", "Architecture Compatibility Analysis", "Platform Documentation Evaluation", "Deployment Environment Testing"],
    "4.3.6": ["Advanced Complexity Analysis", "Code Quality Assessment", "Documentation Quality Evaluation", "Knowledge Distribution Analysis", "Refactoring and Evolution Tracking"],
    "4.3.7": ["Performance Benchmarking Integration", "Environmental Impact Assessment", "Resource Utilization Analysis", "Scalability Assessment", "Optimization Practice Evaluation", "Memory Efficiency Analysis", "I/O Performance Profiling", "Algorithmic Complexity Assessment", "Power Measurement Integration", "Performance Portability Assessment"],
    "4.3.8": ["SBOM Detection", "Build Provenance", "Dependency Vulnerability Posture", "Dependency Freshness"],
}


@dataclass
class Context:
    """What a section builder reads: each dimension's raw result
    (`dims`) and, for convenience, the sub-collector results of each."""
    dims: Dict
    eco: Dict = field(init=False)
    qual: Dict = field(init=False)
    impact_sub: Dict = field(init=False)

    def __post_init__(self):
        self.eco = self.dims.get("ecosystem", {}).get("sub_results", {})
        self.qual = self.dims.get("quality", {}).get("sub_results", {})
        self.impact_sub = self.dims.get("impact", {}).get("sub_results") or {}


def sub_row(sub: Dict, key: str) -> str:
    """Render one collector sub-score as a section row (plus any detail)."""
    info = sub.get(key, {})
    label = info.get("label", key)
    # A sub-score the collector didn't return isn't a measured fail.
    if info.get("not_applicable") or info.get("insufficient_sample") or info.get("unmeasured"):
        # Reported, but neither a pass nor a fail -- no mark.
        return f'<p><strong>{label}:</strong> {info.get("value", "Not applicable")}</p>'
    if not info or info.get("not_collected"):
        return f'<p><strong>{label}:</strong> Not yet collected</p>'
    mark = "✓" if info.get("passing") else "✗"
    row = f'<p><strong>{label}:</strong> {info.get("value", "N/A")} {mark}</p>'
    detail = info.get("detail")
    if detail:
        # A collector may give a list (supply chain's vulnerable
        # dependencies); a str() of it would show Python brackets.
        if isinstance(detail, (list, tuple)):
            detail = ", ".join(str(d) for d in detail)
        row += f'\n<p class="sub-detail">{detail}</p>'
    return row


def score_line(overall: Dict) -> str:
    """Render a collector's own overall_score as a 'Score: X/Y' row.

    Uses the collector's own max_score rather than a hardcoded
    constant, since a permanently-uncollected submetric (see the
    not_collected work across collectors/ecosystem/) is excluded
    from it and it can be smaller than the section's full submetric
    count. A None score (everything gapped) renders as text instead
    of "None/N".
    """
    score = overall.get("score")
    max_score = overall.get("max_score", 0)
    shown = score if score is not None else "Not collected"
    return f'<p><strong>Score:</strong> {shown}/{max_score}</p>'


_MAIN_ROW = re.compile(r'<p(?! class)[^>]*><strong>(?!Score:)[^<]+:</strong>')
_SCORE_LINE = re.compile(r'<p[^>]*><strong>Score:</strong>[^<]*</p>')


def rescore_section(html: Optional[str]) -> Optional[str]:
    """Recompute a section's "Score: X/Y" line from its rendered rows.

    X is the rows marked ✓; Y is the rows marked ✓ or ✗. A row with no
    mark -- "Not yet collected", a gap, a config "N/A", a sample too thin
    to judge -- is neither a pass nor a fail, so it is left out of Y.
    Sections used to hard-code Y as their full row count, so 4.2.7, 4.2.9
    and 4.3.4 read 0/5, 1/5 and 2/5 for packages that passed every row
    actually measured, while 4.2.5 and 4.2.8 already excluded them.
    Numerators are unchanged: across all 70 packages the ✓ count already
    matched every section's own tally.
    """
    if not html or not _SCORE_LINE.search(html):
        return html
    rows = [l for l in html.split("\n") if _MAIN_ROW.match(l.strip())]
    filled = sum(1 for l in rows if "✓" in l)
    scored = sum(1 for l in rows if "✓" in l or "✗" in l)
    shown = f"{filled}/{scored}" if scored else "Not collected"
    return _SCORE_LINE.sub(lambda _: f'<p><strong>Score:</strong> {shown}</p>', html, count=1)


def citation_repo_stats(impact_sub: Dict) -> Dict:
    """Stars/forks from the citation collector. Results stored before the
    forge refactor carry them as "github_stats" with no platform."""
    return impact_sub.get("repo_stats") or impact_sub.get("github_stats") or {}


def apply_section_overrides(html: Optional[str], section_overrides: Dict[str, str]) -> Optional[str]:
    """Replace sub-metric lines in a section's HTML with config-supplied values.

    Any <p><strong>Label:</strong> ...> line whose label appears in section_overrides
    is replaced with the configured value. The Score line is then recalculated from
    the updated HTML so blade counts remain correct.
    """
    if not html or not section_overrides:
        return html

    lines = html.split('\n')
    new_lines = []
    for line in lines:
        matched = False
        for label, value in section_overrides.items():
            if re.match(rf'<p(?! class)[^>]*><strong>{re.escape(label)}:</strong>', line.strip()):
                new_lines.append(f'<p><strong>{label}:</strong> {value}</p>')
                matched = True
                break
        if not matched:
            new_lines.append(line)

    return rescore_section('\n'.join(new_lines))


def build_stub_section(section_num: str, overrides: Dict[str, str]) -> str:
    """Build a placeholder HTML block for a section with no collector yet.

    Sub-metrics default to 'Not yet collected' unless an override is provided.
    """
    submetrics = SECTION_SUBMETRICS.get(section_num, [])
    total = len(submetrics)
    lines = []
    for sm in submetrics:
        value = overrides.get(sm, "Not yet collected")
        lines.append(f"<p><strong>{sm}:</strong> {value}</p>")
    lines.append(f"<p><strong>Score:</strong> 0/{total}</p>")
    return "\n".join(lines)
