"""Dashboard sections for the Ecosystem dimension (CASS report 4.2)."""

from typing import Optional

from collectors.common import get_threshold
from formatters.dashboard.common import Context, score_line, sub_row


def section_4_2_1(ctx: Context) -> Optional[str]:
    eco = ctx.eco
    # --- 4.2.1 CoC, Governance, and Contributor Guidelines (PDF §4.2.1 — 5 sub-metrics) ---
    # 1. Enhanced Document Detection  2. Governance Keyword Analysis
    # 3. OpenSSF Badge Integration    4. CHAOSS Governance Metrics
    # 5. Governance Effectiveness Assessment
    governance = eco.get("governance", {})
    scorecard  = eco.get("openssf_scorecard", {})
    chaoss     = eco.get("chaoss_activity", {})
    gov_lines  = []
    gov_pts    = 0
    if governance or scorecard:
        # 1. Enhanced Document Detection — CoC, Governance, Contributing files
        if governance:
            doc_found = 0
            doc_sub_lines = []
            for label, key in [
                ("Code of Conduct", "code_of_conduct"),
                ("Governance", "governance"),
                ("Contributing Guidelines", "contributing_guidelines"),
            ]:
                info = governance.get(key, {})
                if info.get("exists"):
                    url = info.get("url", "")
                    link = f'<a href="{url}">{info.get("file_path", "")}</a>' if url else info.get("file_path", "")
                    doc_sub_lines.append(f'<p class="sub-detail">{label}: {link}</p>')
                    doc_found += 1
                elif info.get("not_collected"):
                    # The lookup failed; the score already leaves it out.
                    doc_sub_lines.append(f'<p class="sub-detail">{label}: Unavailable this run</p>')
                else:
                    doc_sub_lines.append(f'<p class="sub-detail">{label}: Not found</p>')
            gov_score = governance.get("overall_score", {})
            doc_total = gov_score.get("max_score", 3)
            # CoC + Contributing is sufficient; Governance is optional.
            passing = doc_found >= get_threshold("4.2.1", "Enhanced Document Detection")
            gov_pts += 1 if passing else 0
            gov_lines.append(
                f'<p><strong>Enhanced Document Detection:</strong> {doc_found}/{doc_total} {"✓" if passing else "✗"}</p>'
            )
            gov_lines.extend(doc_sub_lines)
        else:
            gov_lines.append('<p><strong>Enhanced Document Detection:</strong> Unavailable this run</p>')

        # 2. Governance Keyword Analysis — not yet collected
        # 2. Governance Keyword Analysis — do the documents describe a
        #    decision process, or merely exist?
        kw = governance.get("keyword_analysis", {})
        groups = kw.get("groups_found", [])
        kw_ok = len(groups) >= get_threshold("4.2.1", "Governance Keyword Analysis")
        gov_pts += 1 if kw_ok else 0
        gov_lines.append(
            f'<p><strong>Governance Keyword Analysis:</strong> '
            f'{len(groups)}/4 concepts documented {"✓" if kw_ok else "✗"}</p>'
        )
        if groups:
            gov_lines.append(f'<p class="sub-detail">{", ".join(groups)}</p>')

        # 3. OpenSSF Badge Integration — use Scorecard as proxy
        if scorecard and scorecard.get("scorecard_exists"):
            sc_val = scorecard.get("score")
            sc_url = scorecard.get("scorecard_url", "")
            checks = f'{scorecard.get("checks_passed", 0)}/{scorecard.get("checks_total", 0)} checks passed'
            # Reused below for the per-check breakdown too, deliberately:
            # a check listed as "failing" should always be one that's
            # actually below the same bar the overall pass/fail uses,
            # not an independent number that could drift from it.
            min_score = get_threshold("4.2.1", "OpenSSF Badge Integration", "min_score")
            passing = sc_val is not None and sc_val >= min_score
            gov_pts += 1 if passing else 0
            mark = "✓" if passing else "✗"
            link = f'<a href="{sc_url}">{sc_val}/10</a>' if sc_url else f'{sc_val}/10'
            gov_lines.append(f'<p><strong>OpenSSF Scorecard:</strong> {link} ({checks}) {mark}</p>')
            # Exclude Scorecard's -1 "not applicable" sentinel — those
            # aren't real failures, just checks that don't apply here.
            failing_checks = {
                name: info for name, info in scorecard.get("checks", {}).items()
                if 0 <= info.get("score", 0) < min_score
            }
            for name, info in sorted(failing_checks.items(), key=lambda kv: kv[1].get("score", 0)):
                doc_url = info.get("documentation_url", "")
                check_label = f'<a href="{doc_url}">{name}</a>' if doc_url else name
                reason = info.get("reason", "")
                gov_lines.append(
                    f'<p class="sub-detail">{check_label}: {info.get("score", 0)}/10'
                    f'{" — " + reason if reason else ""}</p>'
                )
        else:
            gov_lines.append('<p><strong>OpenSSF Badge Integration:</strong> Unavailable this run</p>')

        # 4. CHAOSS Governance Metrics — weighted CHAOSS health score.
        # CHAOSSGovernanceCollector was already being run for the dimension
        # score; this surfaces it in the section it belongs to.
        if chaoss:
            chaoss_score = chaoss.get("overall_score", {})
            score_val = chaoss_score.get("score", 0)
            status = chaoss_score.get("status", "unknown")
            if score_val is None:
                # Every category unmeasured or gapped: no score to judge.
                gov_lines.append('<p><strong>CHAOSS Governance Metrics:</strong> Unavailable this run</p>')
            else:
                chaoss_ok = score_val >= get_threshold("4.2.1", "CHAOSS Governance Metrics")
                gov_pts += 1 if chaoss_ok else 0
                gov_lines.append(
                    f'<p><strong>CHAOSS Governance Metrics:</strong> '
                    f'{score_val}/100 ({status}) {"✓" if chaoss_ok else "✗"}</p>'
                )
            # Per-category breakdown, weakest first, so the failing areas
            # are what a maintainer sees rather than just the headline score.
            # A category can be {"not_collected": True} instead of a
            # number when its own fetch gapped (see chaoss_governance.py
            # _calculate_overall_score) — those aren't comparable to a
            # score and are listed last rather than sorted by it.
            cats = chaoss_score.get("category_scores", {})
            scored_cats = {k: v for k, v in cats.items() if isinstance(v, (int, float))}
            gapped_cats = [k for k, v in cats.items() if not isinstance(v, (int, float))]
            for name, val in sorted(scored_cats.items(), key=lambda kv: kv[1]):
                gov_lines.append(
                    f'<p class="sub-detail">{name.replace("_", " ").title()}: {round(val)}/100</p>'
                )
            for name in gapped_cats:
                gov_lines.append(
                    f'<p class="sub-detail">{name.replace("_", " ").title()}: Unavailable this run</p>'
                )
        else:
            gov_lines.append('<p><strong>CHAOSS Governance Metrics:</strong> Unavailable this run</p>')

        # 5. Governance Effectiveness Assessment — is governance live?
        #    Owners assigned, and the documents still being maintained.
        eff = governance.get("effectiveness", {})
        eff_signals = []
        # Name the missing half too: "docs updated 53 days ago ✗" alone
        # didn't say why the row failed.
        eff_signals.append("CODEOWNERS defined" if eff.get("has_codeowners") else "no CODEOWNERS")
        days = eff.get("days_since_governance_update")
        if days is not None:
            eff_signals.append(f"docs updated {days} days ago")
        eff_ok = bool(eff.get("has_codeowners")) and bool(eff.get("maintained"))
        gov_pts += 1 if eff_ok else 0
        gov_lines.append(
            f'<p><strong>Governance Effectiveness Assessment:</strong> '
            f'{"; ".join(eff_signals)} '
            f'{"✓" if eff_ok else "✗"}</p>'
        )

        gov_lines.append(f'<p><strong>Score:</strong> {gov_pts}/5</p>')
    section_421_data = "\n".join(gov_lines) if gov_lines else None
    return section_421_data


def section_4_2_2(ctx: Context) -> Optional[str]:
    eco = ctx.eco
    # --- 4.2.2 Licensing and FAIR Compliance (PDF §4.2.2 — 5 sub-metrics) ---
    # 1. Enhanced License Detection  2. Automated FAIR4RS Assessment
    # 3. OSI License Validation      4. License Exception Handling
    # 5. FAIR Metadata Assessment
    licensing = eco.get("licensing", {})
    analysis  = licensing.get("license_analysis", {})
    compliance = licensing.get("compliance_score", {})
    if licensing:
        spdx_id = analysis.get("spdx_id") or ""
        if spdx_id in ("NOASSERTION", ""):
            spdx_id = None
        # GitHub returns NOASSERTION for any licence it cannot match verbatim.
        # Fall back to the family recovered from the licence text so a project
        # with extra copyright notices is not recorded as unlicensed.
        fair_lic = eco.get("fair_licensing", {})
        flsub = fair_lic.get("overall_score", {}).get("sub_scores", {})
        resolved = fair_lic.get("license_exceptions", {}).get("resolved_from_text")
        license_name = spdx_id or resolved or analysis.get("license_type") or "Unknown"
        # Keep the category consistent with whichever name is being shown;
        # every family the text matcher recognises is permissive or copyleft.
        lic_category = analysis.get("category", "Unknown")
        if lic_category in ("Unknown", None, "") and resolved:
            lic_category = (
                "Copyleft" if resolved.startswith(("GPL", "LGPL", "MPL"))
                else "Permissive"
            )
        lic_pts  = 0
        lic_lines = []

        # 1. Enhanced License Detection — passes if file found AND identified
        file_found = bool(compliance.get("details", []) and
                          any("exists" in d for d in compliance.get("details", [])))
        identified = license_name not in ("Unknown", "")
        ld_passing = identified  # file found and identified
        lic_pts += 1 if ld_passing else 0
        lic_lines += [
            f'<p><strong>Enhanced License Detection:</strong> {"✓" if ld_passing else "✗"}</p>',
            f'<p class="sub-detail">License: {license_name}</p>',
            f'<p class="sub-detail">Category: {lic_category}</p>',
        ]

        # 2. Automated FAIR4RS Assessment
        if fair_lic:
            lic_lines.append(sub_row(flsub, "fair4rs_assessment"))
            if flsub.get("fair4rs_assessment", {}).get("passing"):
                lic_pts += 1
        else:
            lic_lines.append('<p><strong>Automated FAIR4RS Assessment:</strong> Unavailable this run</p>')

        # 3. OSI License Validation — passes if osi_approved is True
        osi = analysis.get("osi_approved")
        if osi is None and resolved:
            # Every family the text matcher can name is OSI-approved.
            osi = True
        osi_label = "Yes" if osi is True else ("No" if osi is False else "Unknown")
        if osi is True and resolved and not spdx_id:
            via = fair_lic.get("license_exceptions", {}).get("resolved_via")
            where = "CITATION.cff" if via == "citation" else "licence text"
            osi_label = f"Yes (via {resolved} in {where})"
        osi_passing = osi is True
        lic_pts += 1 if osi_passing else 0
        lic_lines.append(
            f'<p><strong>OSI License Validation:</strong> {osi_label} {"✓" if osi_passing else "✗"}</p>'
        )

        # 4–5. License exceptions and citation metadata
        if fair_lic:
            for key in ["license_exception_handling", "fair_metadata"]:
                lic_lines.append(sub_row(flsub, key))
                if flsub.get(key, {}).get("passing"):
                    lic_pts += 1
        else:
            lic_lines.append('<p><strong>License Exception Handling:</strong> Unavailable this run</p>')
            lic_lines.append('<p><strong>FAIR Metadata Assessment:</strong> Unavailable this run</p>')

        lic_lines.append(f'<p><strong>Score:</strong> {lic_pts}/5</p>')
        section_422_data = "\n".join(lic_lines)
    else:
        section_422_data = None
    return section_422_data


def section_4_2_3(ctx: Context) -> Optional[str]:
    eco = ctx.eco
    # --- 4.2.3 Active Maintenance (PDF §4.2.3 — 6 sub-metrics) ---
    # 1. Commit Activity Pattern Analysis   2. Maintenance Mode Indicator Detection
    # 3. Activity Trend Monitoring          4. Release Pattern Assessment
    # 5. Multi-Channel Communication        6. Contributor Abandonment Forecasting
    maintenance = eco.get("maintenance", {})
    if maintenance:
        maint_lines = []
        maint_pts   = 0
        indicators  = maintenance.get("maintenance_indicators", {})
        commits     = maintenance.get("commit_activity", {})
        releases    = maintenance.get("release_activity", {})
        contribs    = maintenance.get("contributor_activity", {})
        score       = maintenance.get("score", {})

        # 1. Commit Activity Pattern Analysis
        status = "Archived" if indicators.get("archived") else (
            ", ".join(indicators["maintenance_signals"]) if indicators.get("maintenance_signals") else "Active"
        )
        commit_ok = commits.get("total_commits_52w", 0) > 0
        maint_pts += 1 if commit_ok else 0
        maint_lines.append(f'<p><strong>Commit Activity Pattern Analysis:</strong> {status} '
                            f'— {commits.get("total_commits_52w", 0):,} commits (52 weeks) '
                            f'{"✓" if commit_ok else "✗"}</p>')
        if commits.get("days_since_last_commit") is not None:
            maint_lines.append(f'<p class="sub-detail">Last Commit: {commits["days_since_last_commit"]} days ago</p>')

        # 2. Maintenance Mode Indicator Detection
        not_archived = not indicators.get("archived") and not indicators.get("maintenance_signals")
        maint_pts += 1 if not_archived else 0
        maint_lines.append(f'<p><strong>Maintenance Mode Indicator Detection:</strong> '
                            f'No maintenance flags {"✓" if not_archived else "✗"}</p>')

        # 3. Activity Trend Monitoring
        trend = commits.get("recent_trend", "unknown")
        trend_ok = trend in get_threshold("4.2.3", "Activity Trend Monitoring")
        maint_pts += 1 if trend_ok else 0
        maint_lines.append(f'<p><strong>Activity Trend Monitoring:</strong> '
                            f'{trend.capitalize()} {"✓" if trend_ok else "✗"}</p>')

        # 4. Release Pattern Assessment
        rel_count = releases.get("releases_last_year", 0)
        rel_ok = rel_count >= get_threshold("4.2.3", "Release Pattern Assessment")
        maint_pts += 1 if rel_ok else 0
        rel_detail = (f'{releases["latest_release"]} ({releases.get("days_since_latest_release", "?")} days ago)'
                      if releases.get("latest_release") else "No releases")
        maint_lines.append(f'<p><strong>Release Pattern Assessment:</strong> '
                            f'{rel_detail}, {rel_count}/yr {"✓" if rel_ok else "✗"}</p>')

        # Contributor context — sub-detail under Contributor Abandonment Forecasting
        if contribs.get("total_contributors"):
            maint_lines.append(
                f'<p class="sub-detail">Contributors: {contribs["total_contributors"]}'
                f' (bus factor: {contribs.get("bus_factor", 0)})</p>'
            )

        # 5. Multi-Channel Communication Activity
        channels = maintenance.get("channels", {})
        ch_found = channels.get("found", [])
        ch_ok = len(ch_found) >= get_threshold("4.2.3", "Multi-Channel Communication Activity", "min_channels")
        maint_pts += 1 if ch_ok else 0
        maint_lines.append(
            f'<p><strong>Multi-Channel Communication Activity:</strong> '
            f'{len(ch_found)} channel{"s" if len(ch_found) != 1 else ""} '
            f'{"✓" if ch_ok else "✗"}</p>'
        )
        if ch_found:
            maint_lines.append(f'<p class="sub-detail">{", ".join(ch_found)}</p>')

        # 6. Contributor Abandonment Forecasting — contributors active in the
        #    previous year who have since stopped committing.
        ab = maintenance.get("abandonment", {})
        if ab.get("measurable"):
            rate = ab.get("departure_rate", 0)
            ab_ok = rate <= get_threshold("4.2.3", "Contributor Abandonment Forecasting")
            maint_pts += 1 if ab_ok else 0
            maint_lines.append(
                f'<p><strong>Contributor Abandonment Forecasting:</strong> '
                f'{ab.get("departed", 0)} of {ab.get("previously_active", 0)} '
                f'previously active contributors stopped ({rate * 100:.0f}%) '
                f'{"✓" if ab_ok else "✗"}</p>'
            )
            if ab.get("repositories"):
                maint_lines.append(
                    f'<p class="sub-detail">Across {", ".join(ab["repositories"])}</p>')
        else:
            # Needs a full year of activity before the year being judged;
            # unmeasurable is excluded, not scored as a failure.
            maint_lines.append(
                '<p><strong>Contributor Abandonment Forecasting:</strong> '
                'Not enough contributor history to assess (needs two years)</p>'
            )

        maint_lines.append(f'<p><strong>Score:</strong> {maint_pts}/6</p>')
        section_423_data = "\n".join(maint_lines) if maint_lines else None
    else:
        section_423_data = None
    return section_423_data


def section_4_2_4(ctx: Context) -> Optional[str]:
    eco = ctx.eco
    # --- 4.2.4 Engagement ---
    engagement = eco.get("engagement", {})
    if engagement:
        eng_score = engagement.get("overall_score", {})
        sub = eng_score.get("sub_scores", {})
        eng_lines = []

        def _fmt_sub(key: str) -> str:
            s = sub.get(key, {})
            label = s.get("label", key)
            if s.get("not_collected"):
                return f'<p><strong>{label}:</strong> Unavailable this run</p>'
            val = s.get("value", "N/A")
            if s.get("insufficient_sample"):
                # Reported but unscored; carries no mark so the row
                # isn't counted by the Score-line recount either.
                return f'<p><strong>{label}:</strong> {val}</p>'
            mark = "✓" if s.get("passing") else "✗"
            return f'<p><strong>{label}:</strong> {val} {mark}</p>'

        for key in [
            "response_time_tracking",
            "issue_resolution",
            "pr_flow",
            "support_closure",
            "engagement_quality",
            "communication_patterns",
            "community_participation",
        ]:
            eng_lines.append(_fmt_sub(key))

        # PR cycle time — sub-detail under PR Flow Assessment
        pr_stats = engagement.get("pr_stats", {})
        mpr_ct = pr_stats.get("median_cycle_time_hours")
        if mpr_ct is not None:
            eng_lines.append(f'<p class="sub-detail">Median PR Cycle Time: {mpr_ct:.0f} hours</p>')

        eng_lines.append(
            f'<p><strong>Score:</strong> {eng_score.get("score", 0)}/{eng_score.get("max_score", 7)}</p>'
        )
        section_424_data = "\n".join(eng_lines) if eng_lines else None
    else:
        section_424_data = None
    return section_424_data


def section_4_2_10(ctx: Context) -> Optional[str]:
    eco = ctx.eco
    maintenance = eco.get("maintenance", {})
    # --- 4.2.10 Project Longevity and Community Health (PDF §4.2.10 — 5 sub-metrics) ---
    # 1. Comprehensive Activity Analysis   2. Contributor Viability Assessment
    # 3. Maintenance Mode Detection        4. Community Health Trends
    # 5. Project Lifecycle Assessment
    #
    # Every value here is re-derived from data ActiveMaintenanceCollector already
    # fetched for 4.2.3 — this section costs no additional API calls. The overlap
    # with 4.2.3 is intentional: the CASS report scores maintenance activity and
    # long-term viability as separate concerns over the same underlying signals.
    section_4210_lines = []
    long_pts = 0
    if maintenance:
        indicators = maintenance.get("maintenance_indicators", {})
        commits    = maintenance.get("commit_activity", {})
        releases   = maintenance.get("release_activity", {})
        contribs   = maintenance.get("contributor_activity", {})

        # 1. Comprehensive Activity Analysis — commits, releases and sustained
        #    activity treated as three dimensions; 2 of 3 counts as viable.
        commits_52w   = commits.get("total_commits_52w", 0)
        active_weeks  = commits.get("active_weeks_52w", 0)
        rel_last_year = releases.get("releases_last_year", 0)
        dims_active = sum([
            commits_52w > 0,
            rel_last_year >= get_threshold("4.2.10", "Comprehensive Activity Analysis", "min_releases_last_year"),
            active_weeks >= get_threshold("4.2.10", "Comprehensive Activity Analysis", "min_active_weeks"),
        ])
        activity_ok = dims_active >= get_threshold("4.2.10", "Comprehensive Activity Analysis", "min_dimensions")
        long_pts += 1 if activity_ok else 0
        section_4210_lines.append(
            f'<p><strong>Comprehensive Activity Analysis:</strong> '
            f'{dims_active}/3 dimensions active {"✓" if activity_ok else "✗"}</p>'
        )
        section_4210_lines.append(
            f'<p class="sub-detail">{commits_52w:,} commits and {rel_last_year} '
            f'release(s) in the last year; active in {active_weeks}/52 weeks</p>'
        )

        # 2. Contributor Viability Assessment — bus factor.
        bus_factor = contribs.get("bus_factor", 0)
        total_contribs = contribs.get("total_contributors", 0)
        viable = bus_factor >= get_threshold("4.2.10", "Contributor Viability Assessment")
        long_pts += 1 if viable else 0
        section_4210_lines.append(
            f'<p><strong>Contributor Viability Assessment:</strong> '
            f'Bus factor {bus_factor} {"✓" if viable else "✗"}</p>'
        )
        section_4210_lines.append(
            f'<p class="sub-detail">{total_contribs} contributors; top contributor '
            f'{contribs.get("top_contributor_pct", 0)}% of commits</p>'
        )

        # 3. Maintenance Mode Detection — archive status, maintenance keywords in
        #    the description, and a year without a push all count as warnings.
        days_since_push = indicators.get("days_since_last_push")
        warnings = []
        if indicators.get("archived"):
            warnings.append("repository archived")
        if indicators.get("maintenance_signals"):
            warnings.extend(indicators["maintenance_signals"])
        if (days_since_push is not None
                and days_since_push > get_threshold("4.2.10", "Maintenance Mode Detection", "max_days_since_push")):
            warnings.append(f"no push in {days_since_push} days")
        no_warnings = not warnings
        long_pts += 1 if no_warnings else 0
        section_4210_lines.append(
            f'<p><strong>Maintenance Mode Detection:</strong> '
            f'{"No warning indicators" if no_warnings else ", ".join(warnings).capitalize()} '
            f'{"✓" if no_warnings else "✗"}</p>'
        )

        # 4. Community Health Trends — 52-week commit trend from /stats/participation.
        trend = commits.get("recent_trend", "unknown")
        trend_ok = trend in get_threshold("4.2.10", "Community Health Trends")
        long_pts += 1 if trend_ok else 0
        section_4210_lines.append(
            f'<p><strong>Community Health Trends:</strong> '
            f'{trend.capitalize()} {"✓" if trend_ok else "✗"}</p>'
        )

        # 5. Project Lifecycle Assessment — stage from project age crossed with
        #    current release activity. Emerging projects haven't yet demonstrated
        #    the longevity this section measures, so they don't score the point.
        #    Age is the longer of the repository's and the code history's, since
        #    migrated projects are far older than their GitHub repo.
        age = indicators.get("project_age_years")
        if age is None:
            age = indicators.get("repo_age_years")
        if age is None:
            stage = "Unknown"
        elif indicators.get("archived"):
            stage = "Retired"
        elif age < 2:
            stage = "Emerging"
        elif age < 5:
            stage = "Growing"
        elif rel_last_year >= 1 or commits_52w > 0:
            stage = "Mature"
        else:
            stage = "Legacy"
        lifecycle_ok = stage in get_threshold("4.2.10", "Project Lifecycle Assessment")
        long_pts += 1 if lifecycle_ok else 0
        section_4210_lines.append(
            f'<p><strong>Project Lifecycle Assessment:</strong> '
            f'{stage} {"✓" if lifecycle_ok else "✗"}</p>'
        )
        # Report both source dates rather than a single age: they can differ by
        # decades for a project that migrated to GitHub, and which one is the
        # honest figure depends on whether history was imported.
        age_parts = []
        if indicators.get("first_commit_date"):
            age_parts.append(
                f'first commit {indicators["first_commit_date"][:10]} '
                f'({indicators.get("history_age_years")} yrs)'
            )
        if indicators.get("created_at"):
            age_parts.append(
                f'GitHub repo created {indicators["created_at"][:10]} '
                f'({indicators.get("repo_age_years")} yrs)'
            )
        age_text = "; ".join(age_parts) if age_parts else "age unknown"
        section_4210_lines.append(f'<p class="sub-detail">Project age: {age_text}</p>')

        section_4210_lines.append(f'<p><strong>Score:</strong> {long_pts}/5</p>')

    section_4210_data = "\n".join(section_4210_lines) if section_4210_lines else None
    return section_4210_data


def section_4_2_5(ctx: Context) -> Optional[str]:
    eco = ctx.eco
    # --- 4.2.5 Outreach (PDF §4.2.5 — 8 sub-metrics) ---
    outreach = eco.get("outreach", {})
    if outreach:
        osub = outreach.get("overall_score", {}).get("sub_scores", {})
        out_lines = [
            sub_row(osub, key) for key in [
                "new_contributor_tracking",
                "contributor_retention",
                "contributor_lifecycle",
                "contribution_type_diversity",
                "good_first_issue",
                "external_event_participation",
                "training_material_integration",
                "onboarding_infrastructure",
            ]
        ]
        out_lines.append(score_line(outreach.get("overall_score", {})))
        section_425_data = "\n".join(out_lines)
    else:
        section_425_data = None
    return section_425_data


def section_4_2_6(ctx: Context) -> Optional[str]:
    eco = ctx.eco
    # --- 4.2.6 Welcomeness (PDF §4.2.6 — 7 sub-metrics) ---
    # Only Decision-Making Visibility is answerable from repository data;
    # the other six need sentiment/tone analysis or maintainer demographics.
    welcomeness = eco.get("welcomeness", {})
    if welcomeness:
        wsub = welcomeness.get("overall_score", {}).get("sub_scores", {})
        wel_lines = [
            sub_row(wsub, key) for key in [
                "chaoss_community_experience",
                "response_quality_tone",
                "communication_sentiment",
                "contributor_journey",
                "language_communication",
                "leadership_representation",
                "decision_making_visibility",
            ]
        ]
        wel_lines.append(score_line(welcomeness.get("overall_score", {})))
        section_426_data = "\n".join(wel_lines)
    else:
        section_426_data = None
    return section_426_data


def section_4_2_7(ctx: Context) -> Optional[str]:
    eco = ctx.eco
    # --- 4.2.7 Collaboration (PDF §4.2.7 — 5 sub-metrics) ---
    collaboration = eco.get("collaboration", {})
    if collaboration:
        csub = collaboration.get("overall_score", {}).get("sub_scores", {})
        collab_lines = [
            sub_row(csub, key) for key in [
                "advanced_dependency_analysis",
                "cross_project_reference",
                "interoperability",
                "collaboration_network",
                "standards_compliance",
            ]
        ]
        collab_lines.append(
            f'<p><strong>Score:</strong> {collaboration.get("overall_score", {}).get("score", 0)}/5</p>'
        )
        section_427_data = "\n".join(collab_lines)
    else:
        section_427_data = None
    return section_427_data


def section_4_2_8(ctx: Context) -> Optional[str]:
    # --- 4.2.8 Financial Sustainability ---
    # One collector serves both 4.2.8 and 4.2.9: they rest on the same
    # funding documentation and contributor-affiliation pass.
    funding = ctx.eco.get("funding", {})
    if not funding:
        return None
    fsub = funding.get("overall_score", {}).get("sub_scores", {})
    fin_lines = [
        sub_row(fsub, key) for key in [
            "funding_documentation",
            "institutional_affiliation",
            "nih_r50",
            "corporate_sponsorship",
            "funding_portfolio",
        ]
    ]
    fin_lines.append(score_line(funding.get("overall_score", {})))
    return "\n".join(fin_lines)


def section_4_2_9(ctx: Context) -> Optional[str]:
    # --- 4.2.9 Institutional Support ---
    # Only Institutional Support Tracking is derivable from the
    # repository. RSE titles, career development and institutional policy
    # need directory data the report itself flags as unautomatable.
    funding = ctx.eco.get("funding", {})
    if not funding:
        return None
    fsub = funding.get("overall_score", {}).get("sub_scores", {})
    inst_info = fsub.get("institutional_support", {})
    inst_row = sub_row(fsub, "institutional_support")
    # A gap here isn't a confirmed miss -- don't let it silently
    # count as 0/5 alongside the genuinely-unimplemented rows below.
    inst_pts = "N/A" if inst_info.get("not_collected") else (
        1 if inst_info.get("passing") else 0
    )
    return "\n".join([
        '<p><strong>RSE Position Detection:</strong> Not yet implemented</p>',
        inst_row,
        '<p><strong>Career Development Indicators:</strong> Not yet implemented</p>',
        '<p><strong>NIH R50 Award Integration:</strong> Not yet implemented</p>',
        '<p><strong>Institutional Policy Analysis:</strong> Not yet implemented</p>',
        f'<p><strong>Score:</strong> {inst_pts}/5</p>',
    ])
