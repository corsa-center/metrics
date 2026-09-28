"""Regression tests for _transform_for_dashboard against not_collected shapes.

2026-09-17: the first scheduled run after the COLLECTION_GAP rollout crashed
with `TypeError: '<' not supported between instances of 'dict' and 'float'`.
chaoss_governance.py's category_scores can now hold {"not_collected": True}
dicts alongside plain numeric scores, and _transform_for_dashboard tried to
sort/round all of them as if they were always numbers. These tests build the
exact not_collected shapes each collector in the COLLECTION_GAP rollout can
now produce and feed them straight into the dashboard transform, so a
regression here fails locally instead of on the next scheduled run.
"""

import pytest

from orchestrator import MetricsOrchestrator


@pytest.fixture
def orchestrator():
    return MetricsOrchestrator(config_path="config/orchestrator.yaml")


def _base_metrics(ecosystem_sub=None, quality_sub=None):
    return {
        "dimensions": {
            "ecosystem": {"sub_results": ecosystem_sub or {}},
            "quality": {"sub_results": quality_sub or {}},
            "impact": {"sub_results": {}},
        }
    }


class TestChaossCategoryScoresGapHandling:
    def test_mixed_numeric_and_gapped_categories_does_not_crash(self, orchestrator):
        # This exact shape is what crashed the 2026-09-17T00:11 scheduled run.
        metrics = _base_metrics(ecosystem_sub={
            "chaoss_activity": {
                "overall_score": {
                    "score": 42.0,
                    "status": "fair",
                    "category_scores": {
                        "project_popularity": 80.0,
                        "documentation_usability": {"not_collected": True},
                        "release_frequency": 10.0,
                    },
                }
            }
        })
        result = orchestrator._transform_for_dashboard("owner/repo", metrics)
        assert result is not None

    def test_all_categories_gapped_does_not_crash(self, orchestrator):
        metrics = _base_metrics(ecosystem_sub={
            "chaoss_activity": {
                "overall_score": {
                    "score": None,
                    "status": "not_collected",
                    "category_scores": {
                        "project_popularity": {"not_collected": True},
                        "documentation_usability": {"not_collected": True},
                    },
                }
            }
        })
        result = orchestrator._transform_for_dashboard("owner/repo", metrics)
        assert result is not None

    def test_gapped_category_is_labeled_not_collected_in_output(self, orchestrator):
        metrics = _base_metrics(ecosystem_sub={
            # This whole section only renders if governance or scorecard is
            # truthy -- a minimal community_health.py-shaped result, unused
            # by the assertions below, gets it past that gate.
            "governance": {"overall_score": {"max_score": 3}},
            "chaoss_activity": {
                "overall_score": {
                    "score": 80.0,
                    "status": "excellent",
                    "category_scores": {
                        "project_popularity": 80.0,
                        "documentation_usability": {"not_collected": True},
                    },
                }
            }
        })
        result = orchestrator._transform_for_dashboard("owner/repo", metrics)
        section_data = result["ecosystem"]["4.2.1"]["data"]
        assert "Not collected" in section_data
        assert "Documentation Usability" in section_data


def _rows(passing, failing=(), gapped=()):
    """sub_scores with the given keys passing / failing / not_collected."""
    rows = {k: {"label": k, "value": "v", "passing": True} for k in passing}
    rows.update({k: {"label": k, "value": "v", "passing": False} for k in failing})
    rows.update({k: {"label": k, "value": None, "passing": False, "not_collected": True}
                 for k in gapped})
    return rows


class TestScoreLineUsesDynamicMaxScore:
    """A section's Score line counts only rows actually measured -- see
    orchestrator._rescore_section. Uncollected placeholders used to inflate
    the denominator (4.2.7 0/5, 4.2.9 1/5, 4.3.4 2/5 for packages passing
    every measured row).
    """

    def test_outreach_score_line_excludes_uncollected_rows(self, orchestrator):
        metrics = _base_metrics(ecosystem_sub={
            "outreach": {"overall_score": {"score": 2, "max_score": 5, "sub_scores": _rows(
                ["new_contributor_tracking", "contributor_lifecycle"],
                ["contributor_retention", "good_first_issue", "onboarding_infrastructure"],
                ["contribution_type_diversity", "external_event_participation", "training_material_integration"],
            )}}
        })
        result = orchestrator._transform_for_dashboard("owner/repo", metrics)
        assert "Score:</strong> 2/5" in result["ecosystem"]["4.2.5"]["data"]

    def test_outreach_fully_gapped_score_renders_as_text_not_none(self, orchestrator):
        metrics = _base_metrics(ecosystem_sub={
            "outreach": {"overall_score": {
                "score": None, "max_score": 0, "percentage": None,
                "status": "not_collected", "sub_scores": {},
            }}
        })
        result = orchestrator._transform_for_dashboard("owner/repo", metrics)
        assert "None/0" not in result["ecosystem"]["4.2.5"]["data"]
        assert "Score:</strong> Not collected" in result["ecosystem"]["4.2.5"]["data"]

    def test_missing_sub_score_is_not_rendered_as_a_fail(self, orchestrator):
        metrics = _base_metrics(ecosystem_sub={
            "welcomeness": {"overall_score": {"score": 1, "max_score": 1,
                                              "sub_scores": _rows(["decision_making_visibility"])}}
        })
        data = orchestrator._transform_for_dashboard("owner/repo", metrics)["ecosystem"]["4.2.6"]["data"]
        assert "✗" not in data
        assert "Score:</strong> 1/1" in data

    def test_funding_score_line_matches_its_rows(self, orchestrator):
        metrics = _base_metrics(ecosystem_sub={
            "funding": {"overall_score": {"score": 3, "max_score": 4, "sub_scores": _rows(
                ["funding_documentation", "institutional_affiliation", "corporate_sponsorship",
                 "institutional_support"],
                ["funding_portfolio"], ["nih_r50"],
            )}}
        })
        result = orchestrator._transform_for_dashboard("owner/repo", metrics)
        assert "Score:</strong> 3/4" in result["ecosystem"]["4.2.8"]["data"]
        assert "Score:</strong> 1/1" in result["ecosystem"]["4.2.9"]["data"]

    def test_collaboration_and_usability_exclude_uncollected_rows(self, orchestrator):
        collab = _rows(["advanced_dependency_analysis", "installation_success"],
                       ["collaboration_network"],
                       ["cross_project_reference", "interoperability", "standards_compliance"])
        usab = _rows(["documentation_completeness"], [],
                     ["user_experience", "accessibility_features", "usage_analytics"])
        metrics = _base_metrics(
            ecosystem_sub={"collaboration": {"overall_score": {"score": 1, "sub_scores": collab}}},
            quality_sub={"usability": {"overall_score": {"score": 1, "sub_scores": usab}}},
        )
        result = orchestrator._transform_for_dashboard("owner/repo", metrics)
        assert "Score:</strong> 1/2" in result["ecosystem"]["4.2.7"]["data"]
        assert "Score:</strong> 2/2" in result["quality"]["4.3.4"]["data"]

    def test_institutional_support_gap_does_not_render_as_confirmed_zero(self, orchestrator):
        metrics = _base_metrics(ecosystem_sub={
            "funding": {"overall_score": {"score": 2, "max_score": 3, "sub_scores": _rows(
                [], [], ["institutional_support"])}}
        })
        section_429 = orchestrator._transform_for_dashboard("owner/repo", metrics)["ecosystem"]["4.2.9"]["data"]
        assert "0/5" not in section_429
        assert "Score:</strong> Not collected" in section_429


class TestRescoreSection:
    def test_counts_marked_rows_only(self):
        from orchestrator import _rescore_section
        html = "\n".join([
            "<p><strong>A:</strong> x ✓</p>",
            '<p class="sub-detail">detail ✓</p>',
            "<p><strong>B:</strong> y ✗</p>",
            "<p><strong>C:</strong> Not yet collected</p>",
            "<p><strong>D:</strong> N/A</p>",
            "<p><strong>Score:</strong> 1/4</p>",
        ])
        assert "<p><strong>Score:</strong> 1/2</p>" in _rescore_section(html)

    def test_sections_without_a_score_line_are_untouched(self):
        from orchestrator import _rescore_section
        html = "<p><strong>Citation Score:</strong> 70.0/100</p>"
        assert _rescore_section(html) == html
        assert _rescore_section(None) is None


class TestStaticAnalysisGapNotShownAsConfirmedFail:
    def test_gapped_codeql_check_does_not_render_as_no_codeql_found(self, orchestrator):
        metrics = _base_metrics(quality_sub={
            "reliability": {},
            "static_analysis": {"has_codeql": False, "not_collected": True},
        })
        result = orchestrator._transform_for_dashboard("owner/repo", metrics)
        section_431 = result["quality"]["4.3.1"]["data"]
        assert "No CodeQL workflow found" not in section_431
        assert "Not yet collected" in section_431

    def test_confirmed_no_codeql_still_renders_as_a_real_negative(self, orchestrator):
        metrics = _base_metrics(quality_sub={
            "reliability": {},
            "static_analysis": {"has_codeql": False},
        })
        result = orchestrator._transform_for_dashboard("owner/repo", metrics)
        assert "No CodeQL workflow found" in result["quality"]["4.3.1"]["data"]


class TestReproducibilityAndAccessibilityGapRows:
    def test_reproducibility_gapped_category_is_not_a_confirmed_fail(self, orchestrator):
        metrics = _base_metrics(quality_sub={
            "reproducibility": {
                "has_fair4rs_metadata": False,
                "has_container": False,
                "uses_semantic_versioning": False,
                "has_dependency_pinning": False,
                "has_reproducibility_docs": False,
                "categories": {
                    "fair4rs_metadata": {"found": [], "not_collected": ["CITATION.cff"]},
                },
            },
        })
        result = orchestrator._transform_for_dashboard("owner/repo", metrics)
        section = result["quality"]["4.3.3"]["data"]
        assert "FAIR4RS Compliance Assessment:</strong> ✗" not in section
        assert "FAIR4RS Compliance Assessment:</strong> Not yet collected" in section

    def test_accessibility_gapped_category_is_not_a_confirmed_fail(self, orchestrator):
        metrics = _base_metrics(quality_sub={
            "accessibility": {
                "has_portable_build_system": False,
                "has_container": False,
                "categories": {
                    "build_systems": {"found": [], "not_collected": ["CMakeLists.txt"]},
                },
            },
        })
        result = orchestrator._transform_for_dashboard("owner/repo", metrics)
        section = result["quality"]["4.3.5"]["data"]
        assert "Portable Build System Detection:</strong> ✗" not in section
        assert "Portable Build System Detection:</strong> Not yet collected" in section


class TestScoreAggregationDoesNotCrashOnNonePercentage:
    """The scores lists in collect_ecosystem_dimension/collect_quality_dimension
    use dict.get(key, 0), which only substitutes the default when the key is
    *missing* -- not when a collector legitimately returns percentage: None
    for a fully-gapped result. sum()/len() over a list containing None
    crashes with a TypeError. These exercise the real async collection path
    with mocked sub-collectors so a regression here fails the same way the
    2026-09-17 run did, rather than only in the dashboard-transform layer.
    """

    @pytest.mark.asyncio
    async def test_ecosystem_dimension_survives_a_fully_gapped_sub_collector(self, orchestrator, monkeypatch):
        import collectors.ecosystem.community_health as community_health_mod

        async def fake_collect(self, package):
            return {"overall_score": {"score": None, "percentage": None, "status": "not_collected"}}

        monkeypatch.setattr(
            community_health_mod.CommunityHealthCollector, "collect", fake_collect
        )
        # Only let "governance" (community_health.py) through, so no other
        # sub-collector makes a real network call.
        monkeypatch.setattr(
            orchestrator, "_sub_enabled",
            lambda group, key, package=None: key == "governance",
        )

        result = await orchestrator.collect_ecosystem_dimension(
            {"name": "x", "repo_url": "https://github.com/o/r", "repository": "o/r"}
        )
        assert result["score"] == 0.0

    @pytest.mark.asyncio
    async def test_quality_dimension_survives_a_fully_gapped_sub_collector(self, orchestrator, monkeypatch):
        import collectors.quality.reliability as reliability_mod

        async def fake_collect(self, package):
            return {"overall_score": {"score": None, "percentage": None, "status": "not_collected"}}

        monkeypatch.setattr(reliability_mod.ReliabilityCollector, "collect", fake_collect)
        monkeypatch.setattr(
            orchestrator, "_sub_enabled",
            lambda group, key, package=None: key == "reliability",
        )

        result = await orchestrator.collect_quality_dimension(
            {"name": "x", "repo_url": "https://github.com/o/r", "repository": "o/r"}
        )
        assert result["score"] == 0.0
