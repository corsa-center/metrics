"""
Quality Dimension Collector

CASS Sustainability Metrics Report v3 - Section 4.3

The Quality dimension evaluates the technical excellence, usability,
and robustness of the software.

Sub-categories (4.3.1 - 4.3.8):
- 4.3.1 Reliability and Robustness
- 4.3.2 Development Practices
- 4.3.3 Reproducibility
- 4.3.4 Usability
- 4.3.5 Accessibility
- 4.3.6 Maintainability and Understandability
- 4.3.7 Performance and Efficiency
- 4.3.8 Software Supply Chain Integrity
"""

from typing import Dict, Optional

from collectors.dimension import DimensionCollector, SubCollector, percentage_at


def _coverage_score(result: Dict) -> Optional[float]:
    """Counted only when a coverage report was found."""
    if not result.get("coverage_exists"):
        return None
    return percentage_at("coverage_percentage")(result)


def _static_analysis_score(result: Dict) -> Optional[float]:
    """All or nothing on CodeQL; a gapped lookup isn't scored."""
    if result.get("has_codeql"):
        return 100
    if result.get("not_collected"):
        return None
    return 0


class QualityDimensionCollector(DimensionCollector):
    """Collector for Quality dimension metrics (CASS Report Section 4.3)"""

    dimension = "quality"
    SUB_COLLECTORS = (
        # 4.3.2 Development Practices -- CI/CD metrics
        SubCollector("ci_cd", "ci_cd", "collectors.quality.development_practices.ci_cd",
                     "CICDMetricsCollector", "CI/CD", percentage_at("percentage")),
        # 4.3.3 Reproducibility -- containers, lock files, FAIR4RS metadata, semver
        SubCollector("reproducibility", "reproducibility", "collectors.quality.reproducibility",
                     "ReproducibilityCollector", "Reproducibility",
                     percentage_at("overall_score", "percentage")),
        # 4.3.5 Accessibility -- portable build systems and containers
        SubCollector("accessibility", "accessibility", "collectors.quality.accessibility",
                     "AccessibilityCollector", "Accessibility",
                     percentage_at("overall_score", "percentage")),
        # 4.3.1 Test Coverage Excellence -- via Codecov public API
        SubCollector("test_coverage", "test_coverage", "collectors.quality.test_coverage",
                     "TestCoverageCollector", "Test coverage", _coverage_score),
        # 4.3.1 Enhanced Security Analysis -- CodeQL workflow presence
        SubCollector("static_analysis", "static_analysis", "collectors.quality.static_analysis",
                     "StaticAnalysisCollector", "Static analysis", _static_analysis_score),
        # 4.3.2 Testing frameworks, code review coverage, tooling integration
        SubCollector("dev_tooling", "dev_tooling", "collectors.quality.development_practices.dev_tooling",
                     "DevToolingCollector", "Dev tooling",
                     percentage_at("overall_score", "percentage")),
        # 4.3.5 Deployment Environment Testing -- CI runner OS families
        SubCollector("deployment_environments", "deployment_environments",
                     "collectors.quality.deployment_environments",
                     "DeploymentEnvironmentCollector", "Deployment environment",
                     percentage_at("overall_score", "percentage")),
        # 4.3.4 Usability -- documentation completeness
        SubCollector("usability", "usability", "collectors.quality.usability",
                     "UsabilityCollector", "Usability",
                     percentage_at("overall_score", "percentage")),
        # 4.3.6 Maintainability -- tree composition, docs, refactoring activity
        SubCollector("maintainability", "maintainability", "collectors.quality.maintainability",
                     "MaintainabilityCollector", "Maintainability",
                     percentage_at("overall_score", "percentage")),
        # 4.3.1 Reliability -- static analysis tools, hardening, defect trend
        SubCollector("reliability", "reliability", "collectors.quality.reliability",
                     "ReliabilityCollector", "Reliability",
                     percentage_at("overall_score", "percentage")),
        # 4.3.8 Software Supply Chain Integrity -- SBOM and build provenance
        SubCollector("supply_chain", "supply_chain", "collectors.quality.supply_chain",
                     "SupplyChainCollector", "Supply chain",
                     percentage_at("overall_score", "percentage")),
    )
