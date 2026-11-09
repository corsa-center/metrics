"""The dimension collectors (collectors/<dimension>/dimension.py, #104):
their sub-collector registries, how each sub-collector's result becomes a
score component, and how collect_all_metrics combines the dimensions.
"""

import asyncio
import importlib
from unittest.mock import AsyncMock, patch

import pytest

from collectors.dimension import percentage_at
from collectors.ecosystem.dimension import EcosystemDimensionCollector
from collectors.impact.dimension import ImpactDimensionCollector
from collectors.quality.dimension import QualityDimensionCollector
from orchestrator import MetricsOrchestrator

PACKAGE = {"name": "p", "repo_url": "https://github.com/o/r", "repository": "o/r"}
ON = {"collectors": {"impact": True, "ecosystem": True, "quality": True}}


def _sub(cls, key):
    return next(s for s in cls.SUB_COLLECTORS if s.result_key == key)


class TestRegistries:
    @pytest.mark.parametrize("cls", [EcosystemDimensionCollector, QualityDimensionCollector])
    def test_every_sub_collector_class_exists(self, cls):
        for sub in cls.SUB_COLLECTORS:
            assert hasattr(importlib.import_module(sub.module), sub.cls), sub

    @pytest.mark.parametrize("cls", [EcosystemDimensionCollector, QualityDimensionCollector])
    def test_toggles_and_result_keys_are_unique(self, cls):
        toggles = [s.toggle for s in cls.SUB_COLLECTORS]
        keys = [s.result_key for s in cls.SUB_COLLECTORS]
        assert len(set(toggles)) == len(toggles)
        assert len(set(keys)) == len(keys)


class TestScoreFunctions:
    def test_percentage_at_ignores_non_numbers(self):
        score = percentage_at("overall_score", "percentage")
        assert score({"overall_score": {"percentage": 40}}) == 40
        assert score({"overall_score": {"percentage": None}}) is None
        assert score({"overall_score": None}) is None
        assert score({}) is None

    def test_engagement_is_points_over_max(self):
        score = _sub(EcosystemDimensionCollector, "engagement").score
        assert score({"overall_score": {"score": 3, "max_score": 7}}) == 43
        assert score({"overall_score": {"score": None, "max_score": 7}}) is None
        assert score({"overall_score": {"score": 3, "max_score": 0}}) is None

    def test_scorecard_reads_top_level_percentage(self):
        score = _sub(EcosystemDimensionCollector, "openssf_scorecard").score
        assert score({"percentage": 62.5}) == 62.5
        assert score({}) is None

    def test_coverage_counts_only_when_a_report_exists(self):
        score = _sub(QualityDimensionCollector, "test_coverage").score
        assert score({"coverage_exists": True, "coverage_percentage": 81.2}) == 81.2
        assert score({"coverage_exists": False, "coverage_percentage": 0}) is None

    def test_static_analysis_is_all_or_nothing_unless_gapped(self):
        score = _sub(QualityDimensionCollector, "static_analysis").score
        assert score({"has_codeql": True}) == 100
        assert score({"has_codeql": False}) == 0
        assert score({"has_codeql": False, "not_collected": True}) is None


class TestCollect:
    def test_disabled_dimension_returns_the_empty_result(self):
        result = asyncio.run(QualityDimensionCollector({}).collect(PACKAGE))
        assert result == {"dimension": "quality", "score": 0.0, "max_score": 100.0}

    def test_a_failing_sub_collector_is_left_out(self, monkeypatch):
        import collectors.quality.reliability as reliability_mod
        import collectors.quality.usability as usability_mod

        async def boom(self, package):
            raise RuntimeError("boom")

        async def ok(self, package):
            return {"overall_score": {"percentage": 80}}

        monkeypatch.setattr(reliability_mod.ReliabilityCollector, "collect", boom)
        monkeypatch.setattr(usability_mod.UsabilityCollector, "collect", ok)
        dim = QualityDimensionCollector(ON)
        monkeypatch.setattr(dim, "sub_enabled",
                            lambda key, package=None: key in ("reliability", "usability"))

        result = asyncio.run(dim.collect(PACKAGE))
        assert set(result["sub_results"]) == {"usability"}
        assert result["score_components"] == {"usability": 80}
        assert result["score"] == 80.0

    def test_impact_falls_back_to_a_placeholder(self, monkeypatch):
        import collectors.impact.citation as citation_mod

        async def boom(self, package):
            raise RuntimeError("boom")

        monkeypatch.setattr(citation_mod.CitationMetricCollector, "collect", boom)
        result = asyncio.run(ImpactDimensionCollector(ON).collect(PACKAGE))
        assert result["score"] == 0.0
        assert result["metadata"]["status"] == "placeholder"


class TestCollectAllMetrics:
    def test_a_dimension_that_raises_keeps_its_default(self):
        # It used to be passed on as the exception object, and the overall
        # score's .get("score") then crashed the whole package.
        o = MetricsOrchestrator(config_path="config/orchestrator.yaml")
        dim = {"dimension": "x", "score": 50.0, "max_score": 100.0}
        with patch.object(o, "_update_repo_info", new=AsyncMock(return_value=(True, ""))), \
             patch.object(o, "collect_dependency_info", new=AsyncMock(return_value={})), \
             patch.object(o.dimensions["impact"], "collect", new=AsyncMock(side_effect=RuntimeError("boom"))), \
             patch.object(o.dimensions["ecosystem"], "collect", new=AsyncMock(return_value=dim)), \
             patch.object(o.dimensions["quality"], "collect", new=AsyncMock(return_value=dim)):
            metrics = asyncio.run(o.collect_all_metrics(dict(PACKAGE)))
        assert metrics["dimensions"]["impact"] == {"dimension": "impact", "score": 0.0, "max_score": 100.0}
        assert metrics["dimensions"]["ecosystem"] == dim
        w = o._metric_weights()
        assert metrics["overall_score"] == round(50 * w["ecosystem"] + 50 * w["quality"])
