"""Dashboard sections for the Quality dimension (CASS report 4.3)."""

from typing import Optional

from collectors.common import get_threshold
from formatters.dashboard.common import Context, sub_row


def section_4_3_1(ctx: Context) -> Optional[str]:
    qual = ctx.qual
    # --- 4.3.1 Reliability and Robustness (PDF §4.3.1 — 5 sub-metrics) ---
    # 1. Advanced Static Analysis    2. Enhanced Security Analysis
    # 3. CERT Guidelines Compliance  4. Test Coverage Excellence
    # 5. Reliability Trend Analysis
    test_coverage = qual.get("test_coverage", {})
    static_analysis = qual.get("static_analysis", {})
    section_431_lines = []
    rel_pts = 0
    if test_coverage or static_analysis:
        # 1. Advanced Static Analysis — defect-finding tools beyond CodeQL
        reliability = qual.get("reliability", {})
        rsub = reliability.get("overall_score", {}).get("sub_scores", {})
        if reliability:
            section_431_lines.append(sub_row(rsub, "advanced_static_analysis"))
            if rsub.get("advanced_static_analysis", {}).get("passing"):
                rel_pts += 1
        else:
            section_431_lines.append(
                '<p><strong>Advanced Static Analysis:</strong> Unavailable this run</p>')

        # 2. Enhanced Security Analysis — CodeQL workflow presence
        if static_analysis:
            if static_analysis.get("has_codeql"):
                rel_pts += 1
                url = static_analysis.get("workflow_url", "")
                scanner = static_analysis.get("scanner") or "CodeQL"
                text = (f"{scanner} enabled (default setup)"
                        if static_analysis.get("workflow_file") == "CodeQL default setup"
                        else f"{scanner} enabled")
                link = f'<a href="{url}">{text}</a>' if url else text
                section_431_lines.append(f'<p><strong>Enhanced Security Analysis:</strong> {link} ✓</p>')
            elif static_analysis.get("not_collected"):
                # A gap here isn't a confirmed "no CodeQL" -- don't
                # report it as a failure alongside real ✗ results.
                section_431_lines.append(
                    '<p><strong>Enhanced Security Analysis:</strong> Unavailable this run</p>'
                )
            else:
                section_431_lines.append(
                    '<p><strong>Enhanced Security Analysis:</strong> No '
                    f'{static_analysis.get("scanners_checked") or "CodeQL"} workflow found ✗</p>'
                )
        else:
            section_431_lines.append('<p><strong>Enhanced Security Analysis:</strong> Unavailable this run</p>')

        # 3. CERT Guidelines Compliance — secure-coding practice indicators
        if reliability:
            section_431_lines.append(sub_row(rsub, "cert_compliance"))
            if rsub.get("cert_compliance", {}).get("passing"):
                rel_pts += 1
        else:
            section_431_lines.append(
                '<p><strong>CERT Guidelines Compliance:</strong> Unavailable this run</p>')

        # 4. Test Coverage Excellence — via Codecov public API
        if test_coverage.get("coverage_exists"):
            pct = test_coverage["coverage_percentage"]
            url = test_coverage.get("coverage_url", "")
            passing = pct >= get_threshold("4.3.1", "Test Coverage Excellence")
            rel_pts += 1 if passing else 0
            mark = "✓" if passing else "✗"
            link = f'<a href="{url}">{pct}%</a>' if url else f'{pct}%'
            section_431_lines.append(
                f'<p><strong>Test Coverage Excellence:</strong> {link} (Codecov) {mark}</p>'
            )
            lines_total = test_coverage.get("lines_total")
            if lines_total:
                section_431_lines.append(
                    f'<p class="sub-detail">{test_coverage.get("lines_covered", 0):,}/{lines_total:,} lines covered</p>'
                )
        elif test_coverage.get("coverage_in_ci"):
            # Measured, just not somewhere this can read -- not a fail.
            section_431_lines.append(
                '<p><strong>Test Coverage Excellence:</strong> Unavailable</p>'
                f'<p class="sub-detail">Coverage is measured in CI '
                f'({test_coverage["coverage_in_ci"]}) but not published to Codecov</p>'
            )
        else:
            section_431_lines.append(
                '<p><strong>Test Coverage Excellence:</strong> No Codecov data found ✗</p>'
            )

        if reliability:
            section_431_lines.append(sub_row(rsub, "reliability_trend"))
            if rsub.get("reliability_trend", {}).get("passing"):
                rel_pts += 1
        else:
            section_431_lines.append(
                '<p><strong>Reliability Trend Analysis:</strong> Unavailable this run</p>')
        section_431_lines.append(f'<p><strong>Score:</strong> {rel_pts}/5</p>')
    section_431_data = "\n".join(section_431_lines) if section_431_lines else None
    return section_431_data


def section_4_3_3(ctx: Context) -> Optional[str]:
    qual = ctx.qual
    # --- 4.3.3 Reproducibility ---
    reproducibility = qual.get("reproducibility", {})
    # --- 4.3.3 Reproducibility (PDF §4.3.3 — 5 sub-metrics) ---
    # 1. FAIR4RS Compliance   2. Containerization   3. Version Control Best Practices
    # 4. Environment Management   5. Reproducibility Documentation
    if reproducibility:
        cats = reproducibility.get("categories", {})
        repr_pts = 0

        def _repr_row(label, passing, detail=None, not_collected=False):
            nonlocal repr_pts
            # A False built on a gap (at least one candidate in this
            # category gapped and nothing was confirmed found/absent)
            # isn't a confirmed miss -- don't count it against repr_pts
            # or show it as a confident ✗ alongside real fails.
            if not passing and not_collected:
                return f'<p><strong>{label}:</strong> Unavailable this run</p>'
            repr_pts += 1 if passing else 0
            mark = "✓" if passing else "✗"
            row = f'<p><strong>{label}:</strong> {mark}</p>'
            if detail:
                row += f'<p class="sub-detail">{detail}</p>'
            return row

        fair4rs_found = cats.get("fair4rs_metadata", {}).get("found", [])
        container_found = cats.get("containers", {}).get("found", [])
        dep_found = cats.get("dependency_pinning", {}).get("found", [])
        env_detail = cats.get("reproducibility_docs", {}).get("details", {}).get("Environment specification", {})
        env_spec = env_detail.get("file") if env_detail.get("exists") else None
        semver = cats.get("semantic_versioning", {})

        repr_lines = [
            _repr_row("FAIR4RS Compliance Assessment",
                      reproducibility.get("has_fair4rs_metadata"),
                      ", ".join(fair4rs_found) if fair4rs_found else None,
                      bool(cats.get("fair4rs_metadata", {}).get("not_collected"))),
            _repr_row("Containerization Excellence",
                      reproducibility.get("has_container"),
                      ", ".join(container_found) if container_found else None,
                      bool(cats.get("containers", {}).get("not_collected"))),
            _repr_row("Version Control Best Practices",
                      reproducibility.get("uses_semantic_versioning"),
                      ", ".join(semver.get("example_tags", [])[:2]) if semver.get("example_tags") else None,
                      bool(semver.get("not_collected"))),
            # A lockfile, or an environment specification (Spack env,
            # conda environment, devcontainer): the report's "dependency
            # management practices, environment specification".
            _repr_row("Environment Management",
                      reproducibility.get("has_dependency_pinning") or env_spec is not None,
                      ", ".join(dep_found + ([env_spec] if env_spec else [])) or None,
                      bool(cats.get("dependency_pinning", {}).get("not_collected"))),
            _repr_row("Reproducibility Documentation",
                      reproducibility.get("has_reproducibility_docs"),
                      ", ".join(cats.get("reproducibility_docs", {}).get("found", []))
                      or None,
                      bool(cats.get("reproducibility_docs", {}).get("not_collected"))),
            f'<p><strong>Score:</strong> {repr_pts}/5</p>',
        ]
        section_433_data = "\n".join(repr_lines)
    else:
        section_433_data = None
    return section_433_data


def section_4_3_2(ctx: Context) -> Optional[str]:
    eco = ctx.eco
    qual = ctx.qual
    # --- 4.3.2 Development Practices (PDF §4.3.2 — 5 sub-metrics) ---
    # 1. CI/CD Effectiveness   2. Testing Framework   3. Code Review Quality
    # 4. Dev Tool Integration  5. Community Contribution Facilitation
    ci_cd = qual.get("ci_cd", {})
    openssf_badge = eco.get("openssf_badge", {})
    section_432_lines = []
    dp_pts = 0
    if ci_cd or openssf_badge:
        # 1. CI/CD Effectiveness Assessment
        if ci_cd:
            cicd_score = ci_cd.get("score", 0)
            cicd_max   = ci_cd.get("max_score", 6)
            passing    = cicd_score > get_threshold("4.3.2", "CI/CD Effectiveness Assessment", "min_score")
            dp_pts    += 1 if passing else 0
            mark       = "✓" if passing else "✗"
            section_432_lines.append(
                f'<p><strong>CI/CD Effectiveness Assessment:</strong> {cicd_score}/{cicd_max} {mark}</p>'
            )
        else:
            section_432_lines.append(
                '<p><strong>CI/CD Effectiveness Assessment:</strong> Unavailable this run</p>'
            )
        # 2–4. Testing, review coverage and tooling integration
        dev_tooling = qual.get("dev_tooling", {})
        if dev_tooling:
            dsub = dev_tooling.get("overall_score", {}).get("sub_scores", {})
            for key in ["testing_framework", "code_review_quality", "dev_tool_integration"]:
                section_432_lines.append(sub_row(dsub, key))
                if dsub.get(key, {}).get("passing"):
                    dp_pts += 1
        else:
            for label in [
                "Testing Framework Excellence",
                "Code Review Quality Analysis",
                "Development Tool Integration",
            ]:
                section_432_lines.append(f'<p><strong>{label}:</strong> Unavailable this run</p>')
        # 5. Community Contribution Facilitation — OpenSSF badge as proxy
        if openssf_badge:
            badge_status = openssf_badge.get("badge_status", {})
            if openssf_badge.get("badge_exists"):
                level = badge_status.get("level", "").capitalize()
                badge_url = badge_status.get("url", "")
                pct = badge_status.get("progress_percentage", 0)
                passing = pct >= get_threshold("4.3.2", "Community Contribution Facilitation")
                dp_pts += 1 if passing else 0
                mark = "✓" if passing else "✗"
                link = f'<a href="{badge_url}">{level}</a>' if badge_url else level
                section_432_lines.append(
                    f'<p><strong>Community Contribution Facilitation:</strong> OpenSSF Badge {link} ({pct:.0f}%) {mark}</p>'
                )
            else:
                badge_url = badge_status.get("url")
                status = (f'<a href="{badge_url}">registered, not started</a>' if badge_url
                          else "not registered")
                section_432_lines.append(
                    f'<p><strong>Community Contribution Facilitation:</strong> OpenSSF Badge {status} ✗</p>'
                )
            for cat_label, cat_key in [
                ("Governance", "governance_criteria"),
                ("Security", "security_criteria"),
                ("Quality", "quality_criteria"),
            ]:
                crit = openssf_badge.get(cat_key, {})
                crit_total = crit.get("count_total", 0)
                if crit_total:
                    section_432_lines.append(
                        f'<p class="sub-detail">{cat_label} criteria: {crit.get("count_found", 0)}/{crit_total} met</p>'
                    )
        else:
            section_432_lines.append(
                '<p><strong>Community Contribution Facilitation:</strong> Unavailable this run</p>'
            )
        section_432_lines.append(f'<p><strong>Score:</strong> {dp_pts}/5</p>')
    section_432_data = "\n".join(section_432_lines) if section_432_lines else None
    return section_432_data


def section_4_3_4(ctx: Context) -> Optional[str]:
    eco = ctx.eco
    qual = ctx.qual
    collaboration = eco.get("collaboration", {})
    # --- 4.3.4 Usability (PDF §4.3.4 — 5 sub-metrics) ---
    # Installation Success Tracking comes from the 4.2.7 collector, which
    # already queried the package registries; repeating that lookup here
    # would double the ecosyste.ms traffic for the same answer.
    usability = qual.get("usability", {})
    if usability:
        usub = usability.get("overall_score", {}).get("sub_scores", {})
        install_info = (collaboration.get("overall_score", {})
                        .get("sub_scores", {}).get("installation_success", {}))
        use_pts = 0
        use_lines = []
        for key in ["user_experience", "documentation_completeness",
                    "accessibility_features"]:
            use_lines.append(sub_row(usub, key))
            if usub.get(key, {}).get("passing"):
                use_pts += 1
        if install_info:
            use_lines.append(sub_row(
                {"installation_success": install_info}, "installation_success"))
            if install_info.get("passing"):
                use_pts += 1
        else:
            use_lines.append(
                '<p><strong>Installation Success Tracking:</strong> Unavailable this run</p>')
        use_lines.append(sub_row(usub, "usage_analytics"))
        use_lines.append(f'<p><strong>Score:</strong> {use_pts}/5</p>')
        section_434_data = "\n".join(use_lines)
    else:
        section_434_data = None
    return section_434_data


def section_4_3_5(ctx: Context) -> Optional[str]:
    qual = ctx.qual
    # --- 4.3.5 Accessibility (PDF §4.3.5 — 5 sub-metrics) ---
    # 1. Portable Build System   2. Container Availability   3. Architecture Compatibility
    # 4. Platform Documentation  5. Deployment Environment Testing
    accessibility = qual.get("accessibility", {})
    deployment_envs = qual.get("deployment_environments", {})
    if accessibility:
        cats = accessibility.get("categories", {})
        acc_pts = 0

        def _acc_row(label, passing, detail=None, not_collected=False):
            nonlocal acc_pts
            # Same "gap isn't a confirmed miss" rule as _repr_row above.
            if not passing and not_collected:
                return f'<p><strong>{label}:</strong> Unavailable this run</p>'
            acc_pts += 1 if passing else 0
            mark = "✓" if passing else "✗"
            row = f'<p><strong>{label}:</strong> {mark}</p>'
            if detail:
                row += f'<p class="sub-detail">{detail}</p>'
            return row

        build_found = cats.get("build_systems", {}).get("found", [])
        if accessibility.get("other_build"):
            build_found = build_found + [accessibility["other_build"]]
        if accessibility.get("python_package"):
            build_found = build_found + [f'pip-installable Python package ({accessibility["python_package"]})']
        container_found = cats.get("containers", {}).get("found", [])
        if accessibility.get("container_image"):
            container_found = container_found + [accessibility["container_image"]]

        # 5. Deployment Environment Testing comes from its own collector, so
        #    it is rendered with _sub_row and scored alongside the _acc_row
        #    rows before the Score line is appended.
        dep_sub = deployment_envs.get("overall_score", {}).get("sub_scores", {})

        acc_lines = [
            _acc_row("Portable Build System Detection",
                     accessibility.get("has_portable_build_system"),
                     ", ".join(build_found) if build_found else None,
                     bool(cats.get("build_systems", {}).get("not_collected"))),
            _acc_row("Container Availability Assessment",
                     accessibility.get("has_container"),
                     ", ".join(container_found) if container_found else None,
                     bool(cats.get("containers", {}).get("not_collected"))),
            (sub_row(dep_sub, "architecture_compatibility") if deployment_envs
             else "<p><strong>Architecture Compatibility Analysis:</strong> Unavailable this run</p>"),
            (sub_row(dep_sub, "platform_documentation") if deployment_envs
             else "<p><strong>Platform Documentation Evaluation:</strong> Unavailable this run</p>"),
            (sub_row(dep_sub, "deployment_environment_testing") if deployment_envs
             else "<p><strong>Deployment Environment Testing:</strong> Unavailable this run</p>"),
        ]
        for key in ["deployment_environment_testing", "architecture_compatibility",
                    "platform_documentation"]:
            if dep_sub.get(key, {}).get("passing"):
                acc_pts += 1
        acc_lines.append(f'<p><strong>Score:</strong> {acc_pts}/5</p>')
        section_435_data = "\n".join(acc_lines)
    else:
        section_435_data = None
    return section_435_data


def section_4_3_6(ctx: Context) -> Optional[str]:
    eco = ctx.eco
    qual = ctx.qual
    maintenance = eco.get("maintenance", {})
    # --- 4.3.6 Maintainability and Understandability (PDF §4.3.6 — 5 sub-metrics) ---
    # 1. Advanced Complexity Analysis   2. Code Quality Assessment
    # 3. Documentation Quality Evaluation   4. Knowledge Distribution Analysis
    # 5. Refactoring and Evolution Tracking
    contributor_activity = maintenance.get("contributor_activity", {}) if maintenance else {}
    maintainability = qual.get("maintainability", {})
    section_436_lines = []
    maint436_pts = 0
    # Either source alone fills rows: the section used to need 4.2.3's
    # contributor data, so collected maintainability results vanished
    # whenever that was missing.
    if contributor_activity or maintainability:
        msub = maintainability.get("overall_score", {}).get("sub_scores", {})
        if maintainability:
            for key in ["complexity_analysis", "code_quality", "documentation_quality"]:
                section_436_lines.append(sub_row(msub, key))
                if msub.get(key, {}).get("passing"):
                    maint436_pts += 1
        else:
            for label in [
                "Advanced Complexity Analysis",
                "Code Quality Assessment",
                "Documentation Quality Evaluation",
            ]:
                section_436_lines.append(f'<p><strong>{label}:</strong> Unavailable this run</p>')

        # 4. Knowledge Distribution Analysis — bus factor (reuses 4.2.3 data)
        if contributor_activity:
            bus_factor = contributor_activity.get("bus_factor", 0)
            top_pct = contributor_activity.get("top_contributor_pct", 0)
            healthy = bus_factor >= get_threshold("4.3.6", "Knowledge Distribution Analysis")
            maint436_pts += 1 if healthy else 0
            mark = "✓" if healthy else "✗"
            section_436_lines.append(
                f'<p><strong>Knowledge Distribution Analysis:</strong> Bus factor {bus_factor} {mark}</p>'
            )
            section_436_lines.append(
                f'<p class="sub-detail">Top contributor: {top_pct}% of commits '
                f'({contributor_activity.get("total_contributors", 0)} total contributors)</p>'
            )
        else:
            section_436_lines.append(
                '<p><strong>Knowledge Distribution Analysis:</strong> Unavailable this run</p>')

        if maintainability:
            section_436_lines.append(sub_row(msub, "refactoring_tracking"))
            if msub.get("refactoring_tracking", {}).get("passing"):
                maint436_pts += 1
        else:
            section_436_lines.append(
                '<p><strong>Refactoring and Evolution Tracking:</strong> Unavailable this run</p>')
        section_436_lines.append(f'<p><strong>Score:</strong> {maint436_pts}/5</p>')
    section_436_data = "\n".join(section_436_lines) if section_436_lines else None
    return section_436_data


def section_4_3_8(ctx: Context) -> Optional[str]:
    eco = ctx.eco
    qual = ctx.qual
    # --- 4.3.8 Software Supply Chain Integrity (PDF §4.3.8 — new section) ---
    supply_chain = qual.get("supply_chain", {})
    section_438_lines = []
    if supply_chain:
        ssub = supply_chain.get("sub_metrics", {})
        for key in [
            "sbom_detection", "build_provenance",
            "dependency_vulnerability_posture", "dependency_freshness",
        ]:
            section_438_lines.append(sub_row(ssub, key))

        # Badge/Scorecard aren't re-measured here — this section leans on
        # the levels already collected under 4.2 for the practices it
        # deliberately doesn't restate (release signing, static analysis,
        # vulnerability response, etc — see collector docstring).
        badge = eco.get("openssf_badge", {})
        scorecard = eco.get("openssf_scorecard", {})
        if badge.get("badge_exists") or scorecard.get("scorecard_exists"):
            parts = []
            if badge.get("badge_exists"):
                level = badge.get("badge_status", {}).get("level") or "in progress"
                parts.append(f"OpenSSF Badge: {level}")
            if scorecard.get("scorecard_exists"):
                parts.append(f'OpenSSF Scorecard: {scorecard.get("score")}/10')
            section_438_lines.append(
                f'<p class="sub-detail">{" · ".join(parts)} '
                f'— practices this section does not measure directly</p>'
            )

        sc_score = supply_chain.get("overall_score", {})
        section_438_lines.append(
            f'<p><strong>Score:</strong> {sc_score.get("score", 0)}/{sc_score.get("max_score", 0)}</p>'
        )
    section_438_data = "\n".join(section_438_lines) if section_438_lines else None
    return section_438_data
