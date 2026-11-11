"""Dashboard sections for the Impact dimension (CASS report 4.1)."""

from typing import Optional

from formatters.dashboard.common import Context, citation_repo_stats


def section_4_1_1(ctx: Context) -> Optional[str]:
    eco = ctx.eco
    impact_sub = ctx.impact_sub
    # --- 4.1.1 Software Citation and Adoption ---
    sub_metrics = impact_sub.get("sub_metrics", {})
    if sub_metrics:
        citation_lines = []
        # Shown at 0 too: the report's evidence under it says which DOIs
        # were looked up, or that none was declared -- the line a team
        # with no citations most needs.
        formal = sub_metrics.get("formal_citations", {})
        if formal.get("not_collected"):
            citation_lines.append('<p><strong>Formal Citations:</strong> Unavailable this run</p>')
        elif formal.get("raw_value") is not None:
            citation_lines.append(
                f'<p><strong>Formal Citations:</strong> {formal["raw_value"]:,}</p>'
            )
        informal = sub_metrics.get("informal_mentions", {})
        if informal.get("not_collected"):
            citation_lines.append('<p><strong>Informal Mentions:</strong> Unavailable this run</p>')
        elif (informal.get("raw_value") or 0) > 0:
            citation_lines.append(
                f'<p><strong>Informal Mentions:</strong> {informal["raw_value"]:,}</p>'
            )
        # The citation collector's "dependent_packages" is the fork
        # count (GitHub exposes no used-by count); the real reverse
        # dependencies are the Reverse-Dependency Analysis row below.
        # It is already listed as Forks below, so it isn't repeated
        # under a name that claims more than it measures.
        dois = sub_metrics.get("doi_resolutions", {})
        if dois.get("not_collected"):
            citation_lines.append('<p><strong>DOI Resolutions:</strong> Unavailable this run</p>')
        elif (dois.get("raw_value") or 0) > 0:
            citation_lines.append(
                f'<p><strong>DOI Resolutions:</strong> {dois["raw_value"]:,}</p>'
            )
        repo_stats = citation_repo_stats(impact_sub)
        if repo_stats.get("stars", 0) > 0 or repo_stats.get("forks", 0) > 0:
            platform = repo_stats.get("platform") or "GitHub"
            citation_lines.append(
                f'<p><strong>{platform} Stars:</strong> {repo_stats.get("stars", 0):,}</p>'
            )
            citation_lines.append(
                f'<p><strong>{platform} Forks:</strong> {repo_stats.get("forks", 0):,}</p>'
            )
        # Reverse-Dependency Analysis and Package-Manager Download Telemetry
        # (new measurement methods, PDF §4.1.1) — sourced from the same
        # ecosyste.ms data collaboration.py already collects for §4.2.7,
        # not re-fetched here. Evidence only, not part of Citation Score.
        collab = eco.get("collaboration", {})
        registries = collab.get("registries", [])
        if registries:
            max_packages = max((r["dependent_packages"] for r in registries), default=0)
            max_repos = max((r["dependent_repos"] for r in registries), default=0)
            citation_lines.append(
                f'<p><strong>Reverse-Dependency Analysis:</strong> '
                f'{max_packages:,} dependent packages, {max_repos:,} dependent repositories</p>'
            )
        downloads = collab.get("package_downloads", {})
        if downloads.get("total", 0) > 0:
            # Named by package: two conda packages ("hdf5", "hdf5-static")
            # read as "conda …, conda …" otherwise.
            by_registry = ", ".join(
                f'{d["name"]} ({d["ecosystem"]}) {d["downloads"]:,} ({d.get("period") or "n/a"})'
                for d in downloads.get("by_registry", [])
            )
            citation_lines.append(
                f'<p><strong>Package-Manager Downloads:</strong> {downloads["total"]:,}</p>'
            )
            if by_registry:
                citation_lines.append(f'<p class="sub-detail">{by_registry}</p>')
        score = impact_sub.get("score", 0)
        citation_lines.append(f'<p><strong>Citation Score:</strong> {score:.1f}/100</p>')
        section_411_data = "\n".join(citation_lines) if citation_lines else None
    else:
        section_411_data = None
    return section_411_data
