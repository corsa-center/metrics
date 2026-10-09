"""Per-package, human-readable report of how each metric was calculated.

The dashboard shows each CASS section as a block of rows; this renders the
same rows for one package as a single standalone HTML page (issue #80), and
adds what the dashboard leaves implicit:

  - the threshold each row was judged against, next to the row
  - which rows show text set by configuration rather than a measurement
  - how the dimension and overall scores were computed, and from what

It is built from the dashboard's own per-section HTML, not from the raw
collector output, so the report can never say something different from
what the dashboard shows. That HTML is parsed back to plain text and
re-escaped here, so nothing collected (commit messages, file names) is
ever emitted as markup.
"""

import html
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from collectors.ecosystem.base import _THRESHOLDS_PATH, get_section_thresholds

DIMENSIONS = [
    ("impact", "Impact"),
    ("ecosystem", "Ecosystem"),
    ("quality", "Quality"),
]

# Sub-collector keys (dimension score components, and config `collectors:`
# keys) as readers know them: the CASS section each one feeds.
COLLECTOR_LABELS = {
    "governance": "Governance documents (4.2.1)",
    "community_health": "Governance documents (4.2.1)",
    "chaoss_activity": "CHAOSS health score (4.2.1)",
    "openssf_scorecard": "OpenSSF Scorecard (4.2.1)",
    "licensing": "Licensing (4.2.2)",
    "fair_licensing": "FAIR licensing (4.2.2)",
    "maintenance": "Active maintenance (4.2.3)",
    "active_maintenance": "Active maintenance (4.2.3)",
    "engagement": "Engagement (4.2.4)",
    "outreach": "Outreach (4.2.5)",
    "welcomeness": "Welcomeness (4.2.6)",
    "collaboration": "Collaboration (4.2.7)",
    "funding": "Financial sustainability (4.2.8)",
    "openssf_badge": "OpenSSF Best Practices badge (4.3.2)",
    "reliability": "Reliability (4.3.1)",
    "static_analysis": "Static analysis (4.3.1)",
    "test_coverage": "Test coverage (4.3.1)",
    "ci_cd": "CI/CD (4.3.2)",
    "dev_tooling": "Development tooling (4.3.2)",
    "reproducibility": "Reproducibility (4.3.3)",
    "usability": "Usability (4.3.4)",
    "accessibility": "Accessibility (4.3.5)",
    "deployment_environments": "Deployment environments (4.3.5)",
    "maintainability": "Maintainability (4.3.6)",
    "supply_chain": "Supply chain integrity (4.3.8)",
}

# Rows whose dashboard label differs from their thresholds.yaml key.
THRESHOLD_ROW_ALIASES = {
    ("4.2.1", "OpenSSF Badge Integration"): "OpenSSF Scorecard",
}

# Rows are usually one <p> per line, but some builders put a row and its
# detail on the same line, so paragraphs are matched rather than lines.
_PARAGRAPH = re.compile(r'<p( class="sub-detail")?[^>]*>(.*?)</p>', re.S)
_LABEL = re.compile(r'\s*<strong>([^<]+):</strong>\s*(.*)', re.S)
_LINK = re.compile(r'<a href="([^"]*)">(.*?)</a>', re.S)
_TAG = re.compile(r'<[^>]+>')
_MARKS = ("✓", "✗")

# Text with links: a list of (text, url-or-None) segments.
Segments = List[Tuple[str, Optional[str]]]


def _segments(fragment: str) -> Segments:
    """One dashboard HTML fragment as plain-text segments, links kept."""
    out: Segments = []
    pos = 0
    for m in _LINK.finditer(fragment):
        out.append((html.unescape(_TAG.sub("", fragment[pos:m.start()])), None))
        out.append((html.unescape(_TAG.sub("", m.group(2))), m.group(1) or None))
        pos = m.end()
    out.append((html.unescape(_TAG.sub("", fragment[pos:])), None))
    return [(text, url) for text, url in out if text]


def _strip_mark(segs: Segments) -> Tuple[Segments, Optional[str]]:
    """Split a trailing ✓/✗ off a row's value."""
    if segs:
        text, url = segs[-1]
        stripped = text.rstrip()
        if url is None and stripped.endswith(_MARKS):
            rest = stripped[:-1].rstrip()
            return (segs[:-1] + ([(rest, None)] if rest else [])), stripped[-1]
    return segs, None


def parse_section(section_html: Optional[str]) -> Tuple[List[Dict], Optional[str]]:
    """A section's dashboard HTML as rows, plus its "Score:" value if any.

    Each row: {label, value (segments), mark ("✓", "✗" or None), details
    (list of segments)}. A paragraph with no bold label is kept as a row
    with label None.
    """
    rows: List[Dict] = []
    score = None
    for is_detail, body in _PARAGRAPH.findall(section_html or ""):
        if is_detail:
            if rows:
                rows[-1]["details"].append(_segments(body))
            continue
        labelled = _LABEL.match(body)
        label = html.unescape(labelled.group(1)).strip() if labelled else None
        value, mark = _strip_mark(_segments(labelled.group(2) if labelled else body))
        if label == "Score":
            score = "".join(text for text, _ in value).strip()
            continue
        rows.append({"label": label, "value": value, "mark": mark, "details": []})
    return rows, score


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


def section_thresholds(section: str, descriptions: Dict[tuple, str]) -> Dict[str, List[str]]:
    """Row label -> the thresholds it was judged against, as text lines.

    Values are the effective ones (config overrides applied); keys are the
    label the row carries on the dashboard.
    """
    result: Dict[str, List[str]] = {}
    for label, value in get_section_thresholds(section).items():
        row_label = THRESHOLD_ROW_ALIASES.get((section, label), label)
        if isinstance(value, dict):
            lines = []
            for param, param_value in value.items():
                desc = descriptions.get((section, label, param))
                lines.append(
                    f"{param.replace('_', ' ')} = {_fmt_value(param_value)}"
                    + (f" — {desc}" if desc else "")
                )
        else:
            desc = descriptions.get((section, label))
            lines = [_fmt_value(value) + (f" — {desc}" if desc else "")]
        result[row_label] = lines
    return result


def _fmt_time(iso: Optional[str]) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError):
        return iso or "unknown"


def _fmt_num(value: Any) -> str:
    return f"{value:.0f}" if isinstance(value, (int, float)) else "–"


def _collector_label(key: str) -> str:
    return COLLECTOR_LABELS.get(key, key.replace("_", " ").capitalize())


def build_report(
    package: str,
    dashboard: Dict,
    metrics: Dict,
    weights: Dict[str, float],
    overrides: Optional[Dict[str, Dict[str, str]]] = None,
) -> Dict:
    """The report's content, independent of how it is rendered."""
    overrides = overrides or {}
    dims = metrics.get("dimensions", {})
    descriptions = threshold_descriptions()

    dimensions = []
    for key, name in DIMENSIONS:
        dim = dims.get(key, {})
        placeholder = dim.get("metadata", {}).get("status") == "placeholder"
        sections = []
        for number, section in dashboard.get(key, {}).items():
            rows, score = parse_section(section.get("data"))
            thresholds = section_thresholds(number, descriptions)
            overridden = overrides.get(number, {})
            for row in rows:
                row["thresholds"] = thresholds.pop(row["label"], [])
                row["overridden"] = row["label"] in overridden
            sections.append({
                "number": number,
                "title": section.get("title", ""),
                "collected": bool(section.get("data")),
                "score": score,
                "rows": rows,
                # Thresholds for rows this package's section didn't render.
                "other_thresholds": thresholds,
            })
        dimensions.append({
            "key": key,
            "name": name,
            "score": dim.get("score"),
            "weight": weights.get(key, 0),
            "placeholder": placeholder,
            "components": dim.get("score_components", {}),
            "sections": sections,
        })

    exclusions = dashboard.get("config_exclusions", {})
    return {
        "package": package,
        "collected": _fmt_time(metrics.get("last_updated")),
        "overall": metrics.get("overall_score"),
        "dimensions": dimensions,
        "excluded": sorted({
            _collector_label(k) for keys in exclusions.values() for k in keys
        }),
    }


# --------------------------------------------------------------------------- #
# HTML                                                                        #
# --------------------------------------------------------------------------- #

_CSS = """
:root { --fg:#1f2328; --muted:#59636e; --bg:#fff; --line:#d1d9e0; --pass:#1a7f37;
        --fail:#cf222e; --note:#9a6700; --chip:#f6f8fa; --link:#0969da; }
@media (prefers-color-scheme: dark) {
  :root { --fg:#e6edf3; --muted:#9198a1; --bg:#0d1117; --line:#3d444d; --pass:#3fb950;
          --fail:#f85149; --note:#d29922; --chip:#151b23; --link:#4493f8; }
}
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
       font:15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }
main { max-width: 60rem; margin: 0 auto; padding: 1.5rem 1rem 4rem; }
a { color: var(--link); overflow-wrap: anywhere; }
h1 { font-size: 1.6rem; margin: 0 0 .25rem; }
h2 { font-size: 1.3rem; margin: 2.5rem 0 .5rem; padding-bottom: .3rem; border-bottom: 1px solid var(--line); }
h3 { font-size: 1.05rem; margin: 1.75rem 0 .5rem; display:flex; gap:.75rem; align-items:baseline; flex-wrap:wrap; }
.meta, .muted { color: var(--muted); }
.box { border: 1px solid var(--line); border-radius: 6px; padding: .75rem 1rem; margin: 1rem 0; background: var(--chip); }
.box p { margin: .25rem 0; }
table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
th, td { text-align: left; padding: .3rem .5rem; border-bottom: 1px solid var(--line); vertical-align: top; }
td.num { text-align: right; white-space: nowrap; }
.score { font-size: .85rem; font-weight: 600; color: var(--muted); white-space: nowrap; }
ul.rows { list-style: none; padding: 0; margin: 0; }
ul.rows > li { display: grid; grid-template-columns: 5.5rem 1fr; gap: .5rem;
               padding: .45rem 0; border-bottom: 1px solid var(--line); }
.mark { font-size: .8rem; font-weight: 600; padding-top: .15rem; }
.pass { color: var(--pass); } .fail { color: var(--fail); } .unscored { color: var(--muted); }
.detail, .threshold, .override { margin: .15rem 0 0; font-size: .9rem; }
.detail { color: var(--muted); }
.threshold { color: var(--muted); }
.threshold b { color: var(--fg); font-weight: 600; }
.override { color: var(--note); }
nav ol { columns: 2 16rem; padding-left: 1.25rem; margin: .5rem 0; }
@media (max-width: 40rem) { ul.rows > li { grid-template-columns: 1fr; gap: 0; } }
"""


def _h(segs: Segments) -> str:
    return "".join(
        f'<a href="{html.escape(url)}">{html.escape(text)}</a>' if url else html.escape(text)
        for text, url in segs
    )


def _anchor(number: str) -> str:
    return "s" + number.replace(".", "-")


def render_html(report: Dict) -> str:
    esc = html.escape
    out = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{esc(report['package'])} — Metrics Report</title>",
        f"<style>{_CSS}</style></head><body><main>",
        f"<h1>Sustainability metrics report: {esc(report['package'])}</h1>",
        f'<p class="meta">Collected {esc(report["collected"])}</p>',
        '<div class="box">',
        "<p><b>How to read this report.</b> Each row is one sub-metric from the CASS "
        "Sustainability Metrics Report, exactly as the dashboard shows it. "
        '<span class="pass">✓ met</span> and <span class="fail">✗ not met</span> compare '
        "the measured value with the threshold listed under it. "
        '<span class="unscored">Not scored</span> rows were reported but not judged — not '
        "yet collected, not applicable, or too little data — and are left out of the "
        "section score, which is ✓ rows out of ✓ and ✗ rows. Grey lines under a row are "
        "the evidence it was based on.</p>",
        "</div>",
    ]

    # Scores and how they combine.
    out.append("<h2>Scores</h2>")
    out.append(
        "<p>A dimension score is the average of the percentage each of its collectors "
        "reports, not a count of the ✓ rows below. The overall score is the weighted "
        "sum of the three dimension scores.</p>"
    )
    out.append('<table><thead><tr><th>Dimension</th><th class="num">Score</th>'
               '<th class="num">Weight</th><th>Averaged over</th></tr></thead><tbody>')
    for dim in report["dimensions"]:
        if dim["placeholder"]:
            score, basis = "0", "Not yet measured — counted as 0 in the overall score"
        else:
            score = _fmt_num(dim["score"])
            basis = "; ".join(
                f"{esc(_collector_label(k))} {_fmt_num(v)}" for k, v in dim["components"].items()
            ) or "No collector returned a score"
        out.append(
            f'<tr><td>{esc(dim["name"])}</td><td class="num">{score}</td>'
            f'<td class="num">{dim["weight"]:.2f}</td><td>{basis}</td></tr>'
        )
    out.append(
        f'<tr><td><b>Overall</b></td><td class="num"><b>{_fmt_num(report["overall"])}</b></td>'
        "<td></td><td></td></tr></tbody></table>"
    )
    if report["excluded"]:
        out.append(
            '<p class="override">Turned off by this package\'s configuration, so not '
            "collected: " + esc(", ".join(report["excluded"])) + "</p>"
        )

    # Contents.
    out.append("<nav><h2>Sections</h2><ol>")
    for dim in report["dimensions"]:
        for sec in dim["sections"]:
            score = f' <span class="muted">({esc(sec["score"])})</span>' if sec["score"] else ""
            out.append(
                f'<li><a href="#{_anchor(sec["number"])}">{esc(sec["number"])} '
                f'{esc(sec["title"])}</a>{score}</li>'
            )
    out.append("</ol></nav>")

    for dim in report["dimensions"]:
        out.append(f'<h2>{esc(dim["name"])} dimension</h2>')
        for sec in dim["sections"]:
            score = f'<span class="score">Score {esc(sec["score"])}</span>' if sec["score"] else ""
            out.append(
                f'<h3 id="{_anchor(sec["number"])}">{esc(sec["number"])} '
                f'{esc(sec["title"])}{score}</h3>'
            )
            if not sec["collected"]:
                out.append('<p class="muted">Not yet collected.</p>')
            else:
                out.append('<ul class="rows">')
                for row in sec["rows"]:
                    out.append(_render_row(row))
                out.append("</ul>")
            if sec["other_thresholds"]:
                out.append('<p class="threshold">Other thresholds in this section, for '
                           "rows not shown above:</p>")
                for label, lines in sec["other_thresholds"].items():
                    out.append(
                        f'<p class="threshold"><b>{esc(label)}:</b> {esc("; ".join(lines))}</p>'
                    )
    out.append("</main></body></html>")
    return "\n".join(out) + "\n"


def _render_row(row: Dict) -> str:
    esc = html.escape
    mark = row["mark"]
    if mark == "✓":
        badge = '<span class="mark pass">✓ met</span>'
    elif mark == "✗":
        badge = '<span class="mark fail">✗ not met</span>'
    else:
        badge = '<span class="mark unscored">Not scored</span>'
    label = f'<b>{esc(row["label"])}:</b> ' if row["label"] else ""
    parts = [f"<li>{badge}<div>{label}{_h(row['value'])}"]
    if row["overridden"]:
        parts.append(
            '<div class="override">Text set by package configuration, not measured.</div>'
        )
    for detail in row["details"]:
        parts.append(f'<div class="detail">{_h(detail)}</div>')
    for i, line in enumerate(row["thresholds"]):
        prefix = "<b>Threshold:</b> " if i == 0 else ""
        parts.append(f'<div class="threshold">{prefix}{esc(line)}</div>')
    parts.append("</div></li>")
    return "".join(parts)


def render_package_report(
    package: str,
    dashboard: Dict,
    metrics: Dict,
    weights: Dict[str, float],
    overrides: Optional[Dict[str, Dict[str, str]]] = None,
) -> str:
    """The full HTML report for one package.

    Args:
        package: the name of the package
        dashboard: the package's _transform_for_dashboard output
        metrics: the package's collect_all_metrics output
        weights: dimension -> weight used for the overall score
        overrides: section -> {label: text} rows whose text came from config
    """
    return render_html(build_report(package, dashboard, metrics, weights, overrides))
