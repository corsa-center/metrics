"""Per-project, human-readable report of how each metric was calculated.

The dashboard shows each CASS section as a block of rows; this renders the
same rows for one package as a single Markdown page, alongside the evidence
each row was based on (the sub-detail lines, with their links), the
thresholds the run judged it against, and which collectors the project's
config turned off -- so a maintainer can check a result against their repo
rather than take a score on trust (issue #80).

It is built from the dashboard's own per-section HTML, not from the raw
collector output, so the report can never say something different from
what the dashboard shows.
"""

import html
import re
from typing import Any, Dict, List, Optional

from collectors.ecosystem.base import _THRESHOLDS_PATH, get_section_thresholds

DIMENSIONS = [
    ("impact", "Impact Dimension"),
    ("ecosystem", "Ecosystem Dimension"),
    ("quality", "Quality Dimension"),
]

# Rows are usually one <p> per line, but some builders put a row and its
# detail on the same line, so paragraphs are matched rather than lines.
_PARAGRAPH = re.compile(r'<p( class="sub-detail")?[^>]*>(.*?)</p>', re.S)
_LABEL = re.compile(r'\s*<strong>([^<]+):</strong>\s*(.*)', re.S)
_LINK = re.compile(r'<a href="([^"]*)">(.*?)</a>')
_TAG = re.compile(r'<[^>]+>')

LEGEND = (
    "How to read this report: ✓ means the measured value met the threshold, "
    "✗ means it did not. A row with no mark was reported but not scored -- "
    "not yet collected, not applicable to this project, or too little data "
    "to judge -- and is left out of the section score. A section's score is "
    "its ✓ rows over its ✓ and ✗ rows. Indented lines are the evidence the "
    "row was based on."
)


def _inline(fragment: str) -> str:
    """One HTML fragment as Markdown text: links kept, other tags dropped."""
    fragment = _LINK.sub(
        lambda m: f"[{_TAG.sub('', m.group(2))}]({m.group(1)})" if m.group(1) else m.group(2),
        fragment,
    )
    return html.unescape(_TAG.sub("", fragment)).strip()


def section_lines(section_html: Optional[str]) -> List[str]:
    """A section's dashboard HTML as Markdown list items."""
    if not section_html:
        return ["- Not yet collected"]
    lines = []
    for is_detail, body in _PARAGRAPH.findall(section_html):
        labelled = _LABEL.match(body)
        if is_detail:
            lines.append(f"  - {_inline(body)}")
        elif labelled:
            lines.append(f"- **{_inline(labelled.group(1))}:** {_inline(labelled.group(2))}".rstrip())
        else:
            lines.append(f"- {_inline(body)}")
    return lines


def threshold_descriptions(path=_THRESHOLDS_PATH) -> Dict[tuple, str]:
    """Inline comments from config/thresholds.yaml, keyed (section, label[, param]).

    The comment beside a value is the only place its meaning is written
    down ("of 4 FAIR principles satisfied, minimum"), so the report reuses
    it instead of restating it. A comment-only line indented past its entry
    continues that entry's comment; block comments above a key are design
    rationale, not a description, and are skipped.
    """
    descriptions: Dict[tuple, str] = {}
    section = label = None
    last_key = None
    last_indent = -1
    for line in path.read_text().splitlines():
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if not stripped:
            last_key = None
            continue
        if stripped.startswith("#"):
            if last_key is not None and indent > last_indent:
                descriptions[last_key] += " " + stripped.lstrip("#").strip()
            else:
                last_key = None
            continue
        if stripped.startswith("- "):
            last_key = None
            continue
        key, _, rest = stripped.partition(":")
        key = key.strip().strip('"')
        comment = rest.partition("#")[2].strip() if "#" in rest else ""
        if indent == 0:
            section, label, full_key = key, None, None
        elif indent == 2:
            label, full_key = key, (section, key)
        else:
            full_key = (section, label, key)
        last_key = None
        if full_key and comment:
            descriptions[full_key] = comment
            last_key, last_indent = full_key, indent
    return descriptions


def _fmt_value(value: Any) -> str:
    if isinstance(value, list):
        return " or ".join(str(v) for v in value)
    return str(value)


def threshold_lines(section: str, descriptions: Dict[tuple, str]) -> List[str]:
    """The thresholds a section was judged against, as Markdown list items."""
    lines = []
    for label, value in get_section_thresholds(section).items():
        if isinstance(value, dict):
            lines.append(f"- {label}:")
            for param, param_value in value.items():
                desc = descriptions.get((section, label, param))
                lines.append(
                    f"  - {param} = {_fmt_value(param_value)}" + (f" ({desc})" if desc else "")
                )
        else:
            desc = descriptions.get((section, label))
            lines.append(f"- {label}: {_fmt_value(value)}" + (f" ({desc})" if desc else ""))
    return lines


def render_project_report(dashboard: Dict, metrics: Dict, weights: Dict[str, float]) -> str:
    """The full Markdown report for one package.

    Args:
        dashboard: the package's _transform_for_dashboard output
        metrics: the package's collect_all_metrics output
        weights: dimension -> weight used for the overall score
    """
    package = dashboard.get("package", "")
    dims = metrics.get("dimensions", {})
    descriptions = threshold_descriptions()

    out = [f"# Sustainability Metrics Report: {package}", ""]
    if metrics.get("last_updated"):
        out.append(f"Collected: {metrics['last_updated']}")
        out.append("")
    parts = ", ".join(
        f"{title.split()[0]} {round(dims.get(key, {}).get('score', 0))}"
        f" × {weights.get(key, 0):.2f}"
        for key, title in DIMENSIONS
    )
    out.append(f"Overall score: {metrics.get('overall_score', 0)}/100 (weighted: {parts})")
    out.append("")
    out.append(LEGEND)
    out.append("")

    exclusions = dashboard.get("config_exclusions", {})
    excluded = [f"{group}/{key}" for group, keys in exclusions.items() for key in keys]
    if excluded:
        out.append(
            "Collectors turned off by this project's configuration: "
            + ", ".join(excluded)
        )
        out.append("")

    for key, title in DIMENSIONS:
        score = dims.get(key, {}).get("score")
        heading = f"## {title}"
        if score is not None:
            heading += f" — {round(score)}/100"
        out.extend([heading, ""])
        for number, section in dashboard.get(key, {}).items():
            out.extend([f"### {number} {section.get('title', '')}", ""])
            out.extend(section_lines(section.get("data")))
            thresholds = threshold_lines(number, descriptions)
            if thresholds:
                out.extend(["", "Thresholds applied:", ""])
                out.extend(thresholds)
            out.append("")
    return "\n".join(out).rstrip() + "\n"
