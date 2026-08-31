#!/usr/bin/env python3
"""
Orchestrator: Coordinates metrics collection and dashboard integration

Collects metrics across the three CASS dimensions defined in the
CASS Sustainability Metrics Report v3:
  - Impact (4.1): Citation, adoption, field research impact
  - Sustainability (4.2): Governance, licensing, maintenance, engagement, etc.
  - Quality (4.3): Reliability, dev practices, reproducibility, usability, etc.

Usage:
    python orchestrator.py --config config.yaml [--dry-run] [--software HDF5]
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import httpx
import re
import yaml

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


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
}

# Directory containing per-package config files (relative to this script)
PACKAGE_CONFIG_DIR = Path(__file__).parent / "package_config"


class MetricsOrchestrator:
    """Orchestrates metrics collection and dashboard integration"""

    def __init__(self, config_path: str):
        """Initialize orchestrator with configuration

        Args:
            config_path: Path to configuration file
        """
        self.config = self._load_config(config_path)
        self.dashboard_base_url = self.config.get(
            "dashboard_base_url", "https://corsa.center/dashboard"
        ).rstrip("/")
        self.output_path = Path(self.config.get("output_path", "./output"))
        self.collectors_enabled = self.config.get("collectors", {})
        # Fine-grained per-sub-collector toggles (see config/orchestrator.yaml).
        self.sustainability_collectors = self.config.get("sustainability_collectors", {})
        self.quality_collectors = self.config.get("quality_collectors", {})

    def _load_config(self, config_path: str) -> Dict:
        """Load configuration from YAML file, resolving ${ENV_VAR} references"""
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
        return self._resolve_env_vars(config)

    def _resolve_env_vars(self, obj):
        """Recursively resolve ${ENV_VAR} references in config values"""
        if isinstance(obj, str):
            return re.sub(
                r"\$\{(\w+)\}",
                lambda m: os.environ.get(m.group(1), ""),
                obj,
            )
        if isinstance(obj, dict):
            return {k: self._resolve_env_vars(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._resolve_env_vars(v) for v in obj]
        return obj

    def _load_package_config(self, repo_name: str) -> Dict:
        """Load per-package config file from package_config/ if it exists.

        Config files are named <owner>_<repo>.yaml, e.g. HDFGroup_hdf5.yaml.
        Returns an empty dict if no config file is found.
        """
        safe_name = repo_name.replace("/", "_")
        config_file = PACKAGE_CONFIG_DIR / f"{safe_name}.yaml"
        if config_file.exists():
            with open(config_file) as f:
                return yaml.safe_load(f) or {}
        return {}

    @staticmethod
    def _apply_section_overrides(html: Optional[str], section_overrides: Dict[str, str]) -> Optional[str]:
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

        result = '\n'.join(new_lines)

        # Recount ✓ hits and total main-metric lines (excludes sub-details and Score)
        filled = sum(1 for l in new_lines if '✓' in l and 'sub-detail' not in l)
        total  = len(re.findall(r'<p(?! class)[^>]*><strong>(?!Score:)[^<]+:</strong>', result))
        result = re.sub(
            r'<p[^>]*><strong>Score:</strong>[^<]*</p>',
            f'<p><strong>Score:</strong> {filled}/{total}</p>',
            result,
        )
        return result

    @staticmethod
    def _build_stub_section(section_num: str, overrides: Dict[str, str]) -> str:
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

    def _fetch_json(self, url: str) -> Optional[Dict]:
        """Fetch a JSON file from a URL

        Args:
            url: URL to fetch

        Returns:
            Parsed JSON as dict, or None on failure
        """
        logger.info(f"Fetching {url}")
        try:
            response = httpx.get(url, timeout=30.0, follow_redirects=True)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to fetch {url}: {e}")
            return None

    def load_software_catalog(self) -> Dict:
        """Load software catalog from the dashboard (fetched via HTTP)

        Returns:
            Dictionary of software packages with metadata
        """
        url = f"{self.dashboard_base_url}/explore/github-data/intReposInfo.json"
        data = self._fetch_json(url)
        if data is None:
            return {}
        return data.get("data", {})

    def prepare_software_list(
        self, filter_software: Optional[str] = None
    ) -> List[Dict]:
        """Prepare list of software packages to process

        Args:
            filter_software: Optional name filter for specific software

        Returns:
            List of software packages with required metadata
        """
        catalog = self.load_software_catalog()

        software_list = []
        for repo_name, metadata in catalog.items():
            if not isinstance(metadata, dict):
                logger.warning(f"Skipping {repo_name}: metadata is not a dict ({type(metadata).__name__})")
                continue
            # Apply filter if specified
            if filter_software and filter_software.lower() not in repo_name.lower():
                continue

            package = {
                "name": metadata.get("name", repo_name),
                "repository": repo_name,
                "repo_url": metadata.get("url", f"https://github.com/{repo_name}"),
                "description": metadata.get("description", ""),
                "homepage": metadata.get("homepageUrl"),
                "license": (metadata.get("licenseInfo") or {}).get("spdxId"),
                "primary_language": (metadata.get("primaryLanguage") or {}).get("name"),
            }
            software_list.append(package)

        logger.info(f"Prepared {len(software_list)} software packages for processing")
        return software_list

    async def collect_impact_dimension(self, package: Dict) -> Dict:
        """Collect Impact dimension metrics (CASS Report Section 4.1)

        Args:
            package: Package metadata dictionary

        Returns:
            Impact dimension metrics dictionary
        """
        if not self.collectors_enabled.get("impact", False):
            return {"dimension": "impact", "score": 0.0, "max_score": 100.0}

        logger.info(f"Collecting Impact dimension for {package['name']}")

        try:
            from collectors.impact.citation import CitationMetricCollector

            collector = CitationMetricCollector(self.config)
            result = await collector.collect(package)
            score = result.get("score", 0) if result else 0
            return {
                "dimension": "impact",
                "score": score,
                "max_score": 100.0,
                "sub_results": result,
            }
        except ImportError:
            pass
        except Exception as e:
            logger.error(f"Citation collection failed for {package['name']}: {e}")

        # Fall back to dimension placeholder
        try:
            from collectors.impact.dimension import ImpactDimensionCollector

            collector = ImpactDimensionCollector(self.config)
            return await collector.collect(package)
        except Exception as e:
            logger.error(f"Impact dimension collection failed for {package['name']}: {e}")
            return {"dimension": "impact", "score": 0.0, "max_score": 100.0}

    def _sub_enabled(self, group: str, key: str) -> bool:
        """Whether an individual sub-collector is enabled.

        Defaults to True when the toggle group or key is absent, so a config
        predating these blocks keeps running every sub-collector.
        """
        toggles = self.sustainability_collectors if group == "sustainability" else self.quality_collectors
        return toggles.get(key, True)

    def _get_github_token(self) -> Optional[str]:
        """Extract GitHub token from resolved config"""
        token = self.config.get("api_credentials", {}).get("github", {}).get("token", "")
        return token if token else None

    async def collect_sustainability_dimension(self, package: Dict) -> Dict:
        """Collect Sustainability dimension metrics (CASS Report Section 4.2)

        Runs all implemented sustainability sub-collectors and combines scores.
        """
        if not self.collectors_enabled.get("sustainability", False):
            return {"dimension": "sustainability", "score": 0.0, "max_score": 100.0}

        logger.info(f"Collecting Sustainability dimension for {package['name']}")

        github_token = self._get_github_token()
        sub_results = {}

        # 4.2.1 CoC, Governance, and Contributor Guidelines
        if self._sub_enabled("sustainability", "community_health"):
            try:
                from collectors.sustainability.community_health import CommunityHealthCollector
                collector = CommunityHealthCollector(github_token=github_token)
                sub_results["governance"] = await collector.collect(package)
            except Exception as e:
                logger.warning(f"Governance collection failed for {package['name']}: {e}")

        # 4.2.2 Licensing and FAIR Compliance
        if self._sub_enabled("sustainability", "licensing"):
            try:
                from collectors.sustainability.licensing import LicensingCollector
                collector = LicensingCollector(github_token=github_token)
                sub_results["licensing"] = await collector.collect(package)
            except Exception as e:
                logger.warning(f"Licensing collection failed for {package['name']}: {e}")

        # 4.2.3 Active Maintenance
        if self._sub_enabled("sustainability", "active_maintenance"):
            try:
                from collectors.sustainability.active_maintenance import ActiveMaintenanceCollector
                collector = ActiveMaintenanceCollector(github_token=github_token)
                sub_results["maintenance"] = await collector.collect(package)
            except Exception as e:
                logger.warning(f"Active maintenance collection failed for {package['name']}: {e}")

        # 4.2.4 CHAOSS Activity Metrics
        if self._sub_enabled("sustainability", "chaoss_activity"):
            try:
                from collectors.sustainability.chaoss_governance import CHAOSSGovernanceCollector
                collector = CHAOSSGovernanceCollector(github_token=github_token)
                sub_results["chaoss_activity"] = await collector.collect(package)
            except Exception as e:
                logger.warning(f"CHAOSS activity collection failed for {package['name']}: {e}")

        # 4.2.5 OpenSSF Best Practices Badge
        if self._sub_enabled("sustainability", "openssf_badge"):
            try:
                from collectors.sustainability.openssf_badge import OpenSSFBadgeCollector
                collector = OpenSSFBadgeCollector(github_token=github_token)
                sub_results["openssf_badge"] = await collector.collect(package)
            except Exception as e:
                logger.warning(f"OpenSSF badge collection failed for {package['name']}: {e}")

        # 4.2.4 Engagement — issue/PR response times, open/close ratios
        if self._sub_enabled("sustainability", "engagement"):
            try:
                from collectors.sustainability.engagement import EngagementCollector
                collector = EngagementCollector(github_token=github_token)
                sub_results["engagement"] = await collector.collect(package)
            except Exception as e:
                logger.warning(f"Engagement collection failed for {package['name']}: {e}")

        # 4.2.5 Outreach — newcomer growth, retention, onboarding infrastructure
        if self._sub_enabled("sustainability", "outreach"):
            try:
                from collectors.sustainability.outreach import OutreachCollector
                collector = OutreachCollector(github_token=github_token)
                sub_results["outreach"] = await collector.collect(package)
            except Exception as e:
                logger.warning(f"Outreach collection failed for {package['name']}: {e}")

        # 4.2.6 Welcomeness — decision-making visibility
        if self._sub_enabled("sustainability", "welcomeness"):
            try:
                from collectors.sustainability.welcomeness import WelcomenessCollector
                collector = WelcomenessCollector(github_token=github_token)
                sub_results["welcomeness"] = await collector.collect(package)
            except Exception as e:
                logger.warning(f"Welcomeness collection failed for {package['name']}: {e}")

        # 4.2.8 + 4.2.9 Funding and institutional affiliation
        if self._sub_enabled("sustainability", "funding"):
            try:
                from collectors.sustainability.funding import FundingCollector
                collector = FundingCollector(github_token=github_token)
                sub_results["funding"] = await collector.collect(package)
            except Exception as e:
                logger.warning(f"Funding collection failed for {package['name']}: {e}")

        # OpenSSF Scorecard
        if self._sub_enabled("sustainability", "openssf_scorecard"):
            try:
                from collectors.sustainability.openssf_scorecard import OpenSSFScorecardCollector
                collector = OpenSSFScorecardCollector(github_token=github_token)
                sub_results["openssf_scorecard"] = await collector.collect(package)
            except Exception as e:
                logger.warning(f"OpenSSF Scorecard collection failed for {package['name']}: {e}")

        # Calculate combined sustainability score from available sub-collectors
        scores = []
        if "governance" in sub_results:
            scores.append(sub_results["governance"].get("overall_score", {}).get("percentage", 0))
        if "licensing" in sub_results:
            scores.append(sub_results["licensing"].get("compliance_score", {}).get("percentage", 0))
        if "maintenance" in sub_results:
            scores.append(sub_results["maintenance"].get("score", {}).get("percentage", 0))
        if "chaoss_activity" in sub_results:
            scores.append(sub_results["chaoss_activity"].get("overall_score", {}).get("score", 0))
        if "openssf_badge" in sub_results:
            scores.append(sub_results["openssf_badge"].get("overall_score", {}).get("score", 0))
        if "engagement" in sub_results:
            eng_s = sub_results["engagement"].get("overall_score", {})
            mx = eng_s.get("max_score", 7)
            scores.append(round(eng_s.get("score", 0) / mx * 100) if mx else 0)
        if "openssf_scorecard" in sub_results:
            pct = sub_results["openssf_scorecard"].get("percentage")
            if pct is not None:
                scores.append(pct)
        if "outreach" in sub_results:
            scores.append(sub_results["outreach"].get("overall_score", {}).get("percentage", 0))
        if "funding" in sub_results:
            scores.append(sub_results["funding"].get("overall_score", {}).get("percentage", 0))
        if "welcomeness" in sub_results:
            scores.append(sub_results["welcomeness"].get("overall_score", {}).get("percentage", 0))

        avg_score = sum(scores) / len(scores) if scores else 0.0

        return {
            "dimension": "sustainability",
            "score": round(avg_score, 2),
            "max_score": 100.0,
            "sub_results": sub_results,
        }

    async def collect_quality_dimension(self, package: Dict) -> Dict:
        """Collect Quality dimension metrics (CASS Report Section 4.3)

        Includes: reliability, development practices, reproducibility,
        usability, accessibility, maintainability, performance.
        """
        if not self.collectors_enabled.get("quality", False):
            return {"dimension": "quality", "score": 0.0, "max_score": 100.0}

        logger.info(f"Collecting Quality dimension for {package['name']}")

        github_token = self.config.get("api_credentials", {}).get("github", {}).get("token", "")
        sub_results = {}

        # 4.3.2 Development Practices — CI/CD metrics
        if self._sub_enabled("quality", "ci_cd"):
            try:
                from collectors.quality.development_practices.ci_cd import CICDMetricsCollector
                collector = CICDMetricsCollector(self.config)
                sub_results["ci_cd"] = await collector.collect(package)
            except Exception as e:
                logger.warning(f"CI/CD collection failed for {package['name']}: {e}")

        # 4.3.3 Reproducibility — containers, lock files, FAIR4RS metadata, semver
        if self._sub_enabled("quality", "reproducibility"):
            try:
                from collectors.quality.reproducibility import ReproducibilityCollector
                collector = ReproducibilityCollector(github_token=github_token)
                sub_results["reproducibility"] = await collector.collect(package)
            except Exception as e:
                logger.warning(f"Reproducibility collection failed for {package['name']}: {e}")

        # 4.3.5 Accessibility — portable build systems and containers
        if self._sub_enabled("quality", "accessibility"):
            try:
                from collectors.quality.accessibility import AccessibilityCollector
                collector = AccessibilityCollector(github_token=github_token)
                sub_results["accessibility"] = await collector.collect(package)
            except Exception as e:
                logger.warning(f"Accessibility collection failed for {package['name']}: {e}")

        # 4.3.1 Test Coverage Excellence — via Codecov public API
        if self._sub_enabled("quality", "test_coverage"):
            try:
                from collectors.quality.test_coverage import TestCoverageCollector
                collector = TestCoverageCollector(github_token=github_token)
                sub_results["test_coverage"] = await collector.collect(package)
            except Exception as e:
                logger.warning(f"Test coverage collection failed for {package['name']}: {e}")

        # 4.3.5 Deployment Environment Testing — CI runner OS families
        if self._sub_enabled("quality", "deployment_environments"):
            try:
                from collectors.quality.deployment_environments import DeploymentEnvironmentCollector
                collector = DeploymentEnvironmentCollector(github_token=github_token)
                sub_results["deployment_environments"] = await collector.collect(package)
            except Exception as e:
                logger.warning(f"Deployment environment collection failed for {package['name']}: {e}")

        # 4.3.2 Testing frameworks, code review coverage, tooling integration
        if self._sub_enabled("quality", "dev_tooling"):
            try:
                from collectors.quality.development_practices.dev_tooling import DevToolingCollector
                collector = DevToolingCollector(github_token=github_token)
                sub_results["dev_tooling"] = await collector.collect(package)
            except Exception as e:
                logger.warning(f"Dev tooling collection failed for {package['name']}: {e}")

        # 4.3.1 Enhanced Security Analysis — CodeQL workflow presence
        if self._sub_enabled("quality", "static_analysis"):
            try:
                from collectors.quality.static_analysis import StaticAnalysisCollector
                collector = StaticAnalysisCollector(github_token=github_token)
                sub_results["static_analysis"] = await collector.collect(package)
            except Exception as e:
                logger.warning(f"Static analysis collection failed for {package['name']}: {e}")

        scores = []
        if "ci_cd" in sub_results:
            scores.append(sub_results["ci_cd"].get("percentage", 0))
        if "reproducibility" in sub_results:
            scores.append(sub_results["reproducibility"].get("overall_score", {}).get("percentage", 0))
        if "accessibility" in sub_results:
            scores.append(sub_results["accessibility"].get("overall_score", {}).get("percentage", 0))
        if sub_results.get("test_coverage", {}).get("coverage_exists"):
            scores.append(sub_results["test_coverage"].get("coverage_percentage", 0))
        if "static_analysis" in sub_results:
            scores.append(100 if sub_results["static_analysis"].get("has_codeql") else 0)
        if "dev_tooling" in sub_results:
            scores.append(sub_results["dev_tooling"].get("overall_score", {}).get("percentage", 0))
        if "deployment_environments" in sub_results:
            scores.append(
                sub_results["deployment_environments"].get("overall_score", {}).get("percentage", 0)
            )

        avg_score = sum(scores) / len(scores) if scores else 0.0

        return {
            "dimension": "quality",
            "score": round(avg_score, 2),
            "max_score": 100.0,
            "sub_results": sub_results,
        }

    async def collect_all_metrics(self, package: Dict) -> Dict:
        """Collect all metrics for a package across the 3 CASS dimensions

        Args:
            package: Package metadata

        Returns:
            Complete metrics dictionary
        """
        logger.info(
            f"Starting metrics collection for {package['name']} ({package['repository']})"
        )

        # Collect all 3 CASS dimensions in parallel
        (
            impact_metrics,
            sustainability_metrics,
            quality_metrics,
        ) = await asyncio.gather(
            self.collect_impact_dimension(package),
            self.collect_sustainability_dimension(package),
            self.collect_quality_dimension(package),
            return_exceptions=True,
        )

        # Handle exceptions
        if isinstance(impact_metrics, Exception):
            logger.error(f"Impact dimension error: {impact_metrics}")
            impact_metrics = {"dimension": "impact", "score": 0.0, "max_score": 100.0}

        if isinstance(sustainability_metrics, Exception):
            logger.error(f"Sustainability dimension error: {sustainability_metrics}")
            sustainability_metrics = {"dimension": "sustainability", "score": 0.0, "max_score": 100.0}

        if isinstance(quality_metrics, Exception):
            logger.error(f"Quality dimension error: {quality_metrics}")
            quality_metrics = {"dimension": "quality", "score": 0.0, "max_score": 100.0}

        # Calculate overall score (weighted average of 3 dimensions)
        overall_score = self._calculate_overall_score(
            impact_metrics,
            sustainability_metrics,
            quality_metrics,
        )

        return {
            "overall_score": overall_score,
            "dimensions": {
                "impact": impact_metrics,
                "sustainability": sustainability_metrics,
                "quality": quality_metrics,
            },
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    def _calculate_overall_score(
        self,
        impact: Dict,
        sustainability: Dict,
        quality: Dict,
    ) -> int:
        """Calculate weighted overall sustainability score based on 3 CASS dimensions

        Default weights (can be configured):
        - Impact: 33%
        - Sustainability: 34%
        - Quality: 33%
        """
        # Get weights from config or use defaults
        weights = self.config.get("metric_weights", {})
        impact_weight = weights.get("impact", 0.33)
        sustainability_weight = weights.get("sustainability", 0.34)
        quality_weight = weights.get("quality", 0.33)

        # Extract scores from dimension results
        impact_score = impact.get("score", 0)
        sustainability_score = sustainability.get("score", 0)
        quality_score = quality.get("score", 0)

        # Calculate weighted average
        overall = (
            impact_score * impact_weight
            + sustainability_score * sustainability_weight
            + quality_score * quality_weight
        )

        return int(round(overall))

    async def process_all_software(
        self, filter_software: Optional[str] = None, dry_run: bool = False
    ) -> Dict:
        """Process all software packages

        Args:
            filter_software: Optional filter for specific software
            dry_run: If True, don't write output files

        Returns:
            Dictionary of all metrics keyed by repository name
        """
        software_list = self.prepare_software_list(filter_software)
        all_metrics = {}

        for i, package in enumerate(software_list, 1):
            logger.info(f"Processing {i}/{len(software_list)}: {package['name']}")

            try:
                metrics = await self.collect_all_metrics(package)
                all_metrics[package["repository"]] = metrics

                # Rate limiting
                if i < len(software_list):
                    await asyncio.sleep(2)  # 2 second delay between packages

            except Exception as e:
                logger.error(f"Failed to process {package['name']}: {e}")
                continue

        # Write output
        if not dry_run:
            self._write_summary_report(all_metrics)
            self._write_dashboard_output(all_metrics)

        return all_metrics

    def _write_summary_report(self, metrics: Dict):
        """Write summary report"""
        output_file = self.output_path / "orchestrator_summary.json"
        output_file.parent.mkdir(parents=True, exist_ok=True)

        summary = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_packages": len(metrics),
            "avg_overall_score": sum(m["overall_score"] for m in metrics.values())
            / len(metrics)
            if metrics
            else 0,
            "packages_by_score": {
                "excellent (80-100)": sum(
                    1 for m in metrics.values() if m["overall_score"] >= 80
                ),
                "good (60-79)": sum(
                    1 for m in metrics.values() if 60 <= m["overall_score"] < 80
                ),
                "fair (40-59)": sum(
                    1 for m in metrics.values() if 40 <= m["overall_score"] < 60
                ),
                "needs_improvement (0-39)": sum(
                    1 for m in metrics.values() if m["overall_score"] < 40
                ),
            },
            "top_packages": sorted(
                [(k, v["overall_score"]) for k, v in metrics.items()],
                key=lambda x: x[1],
                reverse=True,
            )[:10],
        }

        with open(output_file, "w") as f:
            json.dump(summary, f, indent=2)

        logger.info(f"Summary report written to {output_file}")

    def _transform_for_dashboard(self, repo_name: str, metrics: Dict) -> Dict:
        """Transform internal metrics into the per-package CASS v3 format.

        The dashboard expects per-package files at:
          explore/github-data/{repo}-metrics/metrics.json

        Structure keyed by CASS Report section numbers (4.1.x, 4.2.x, 4.3.x),
        each with a ``title`` and ``data`` (HTML string or None).
        """
        dims = metrics.get("dimensions", {})

        # Load per-package overrides (e.g. N/A values supplied by maintainers)
        pkg_config = self._load_package_config(repo_name)
        pkg_overrides: Dict[str, Dict[str, str]] = pkg_config.get("overrides", {})

        def _stub(section_num: str) -> Optional[str]:
            """Return a stub HTML block if the section has any overrides, else None."""
            if section_num in pkg_overrides:
                return self._build_stub_section(section_num, pkg_overrides[section_num])
            return None

        # --- 4.1.1 Software Citation and Adoption ---
        impact_sub = dims.get("impact", {}).get("sub_results") or {}
        sub_metrics = impact_sub.get("sub_metrics", {})
        if sub_metrics:
            citation_lines = []
            formal = sub_metrics.get("formal_citations", {})
            if formal.get("raw_value", 0) > 0:
                citation_lines.append(
                    f'<p><strong>Formal Citations:</strong> {formal["raw_value"]:,}</p>'
                )
            informal = sub_metrics.get("informal_mentions", {})
            if informal.get("raw_value", 0) > 0:
                citation_lines.append(
                    f'<p><strong>Informal Mentions:</strong> {informal["raw_value"]:,}</p>'
                )
            dependents = sub_metrics.get("dependent_packages", {})
            if dependents.get("raw_value", 0) > 0:
                citation_lines.append(
                    f'<p><strong>Dependent Packages:</strong> {dependents["raw_value"]:,}</p>'
                )
            dois = sub_metrics.get("doi_resolutions", {})
            if dois.get("raw_value", 0) > 0:
                citation_lines.append(
                    f'<p><strong>DOI Resolutions:</strong> {dois["raw_value"]:,}</p>'
                )
            github_stats = impact_sub.get("github_stats", {})
            if github_stats.get("stars", 0) > 0 or github_stats.get("forks", 0) > 0:
                citation_lines.append(
                    f'<p><strong>GitHub Stars:</strong> {github_stats.get("stars", 0):,}</p>'
                )
                citation_lines.append(
                    f'<p><strong>GitHub Forks:</strong> {github_stats.get("forks", 0):,}</p>'
                )
            score = impact_sub.get("score", 0)
            citation_lines.append(f'<p><strong>Citation Score:</strong> {score:.1f}/100</p>')
            section_411_data = "\n".join(citation_lines) if citation_lines else None
        else:
            section_411_data = None

        sust = dims.get("sustainability", {}).get("sub_results", {})

        # --- 4.2.1 CoC, Governance, and Contributor Guidelines (PDF §4.2.1 — 5 sub-metrics) ---
        # 1. Enhanced Document Detection  2. Governance Keyword Analysis
        # 3. OpenSSF Badge Integration    4. CHAOSS Governance Metrics
        # 5. Governance Effectiveness Assessment
        governance = sust.get("governance", {})
        scorecard  = sust.get("openssf_scorecard", {})
        chaoss     = sust.get("chaoss_activity", {})
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
                    else:
                        doc_sub_lines.append(f'<p class="sub-detail">{label}: Not found</p>')
                gov_score = governance.get("overall_score", {})
                doc_total = gov_score.get("max_score", 3)
                passing = doc_found >= 2  # CoC + Contributing is sufficient; Governance is optional
                gov_pts += 1 if passing else 0
                gov_lines.append(
                    f'<p><strong>Enhanced Document Detection:</strong> {doc_found}/{doc_total} {"✓" if passing else "✗"}</p>'
                )
                gov_lines.extend(doc_sub_lines)
            else:
                gov_lines.append('<p><strong>Enhanced Document Detection:</strong> Not yet collected</p>')

            # 2. Governance Keyword Analysis — not yet collected
            gov_lines.append('<p><strong>Governance Keyword Analysis:</strong> Not yet collected</p>')

            # 3. OpenSSF Badge Integration — use Scorecard as proxy (passes if score ≥ 7.0)
            if scorecard and scorecard.get("scorecard_exists"):
                sc_val = scorecard.get("score")
                sc_url = scorecard.get("scorecard_url", "")
                checks = f'{scorecard.get("checks_passed", 0)}/{scorecard.get("checks_total", 0)} checks passed'
                passing = sc_val is not None and sc_val >= 7.0
                gov_pts += 1 if passing else 0
                mark = "✓" if passing else "✗"
                link = f'<a href="{sc_url}">{sc_val}/10</a>' if sc_url else f'{sc_val}/10'
                gov_lines.append(f'<p><strong>OpenSSF Scorecard:</strong> {link} ({checks}) {mark}</p>')
                # Exclude Scorecard's -1 "not applicable" sentinel — those
                # aren't real failures, just checks that don't apply here.
                failing_checks = {
                    name: info for name, info in scorecard.get("checks", {}).items()
                    if 0 <= info.get("score", 0) < 7
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
                gov_lines.append('<p><strong>OpenSSF Badge Integration:</strong> Not yet collected</p>')

            # 4. CHAOSS Governance Metrics — weighted CHAOSS health score.
            # CHAOSSGovernanceCollector was already being run for the dimension
            # score; this surfaces it in the section it belongs to. "Good" (>= 60)
            # is the collector's own threshold for a passing project.
            if chaoss:
                chaoss_score = chaoss.get("overall_score", {})
                score_val = chaoss_score.get("score", 0)
                status = chaoss_score.get("status", "unknown")
                chaoss_ok = score_val >= 60
                gov_pts += 1 if chaoss_ok else 0
                gov_lines.append(
                    f'<p><strong>CHAOSS Governance Metrics:</strong> '
                    f'{score_val}/100 ({status}) {"✓" if chaoss_ok else "✗"}</p>'
                )
                # Per-category breakdown, weakest first, so the failing areas
                # are what a maintainer sees rather than just the headline score.
                cats = chaoss_score.get("category_scores", {})
                for name, val in sorted(cats.items(), key=lambda kv: kv[1]):
                    gov_lines.append(
                        f'<p class="sub-detail">{name.replace("_", " ").title()}: {round(val)}/100</p>'
                    )
            else:
                gov_lines.append('<p><strong>CHAOSS Governance Metrics:</strong> Not yet collected</p>')

            # 5. Not yet collected
            gov_lines.append('<p><strong>Governance Effectiveness Assessment:</strong> Not yet collected</p>')

            gov_lines.append(f'<p><strong>Score:</strong> {gov_pts}/5</p>')
        section_421_data = "\n".join(gov_lines) if gov_lines else None

        # --- 4.2.2 Licensing and FAIR Compliance (PDF §4.2.2 — 5 sub-metrics) ---
        # 1. Enhanced License Detection  2. Automated FAIR4RS Assessment
        # 3. OSI License Validation      4. License Exception Handling
        # 5. FAIR Metadata Assessment
        licensing = sust.get("licensing", {})
        analysis  = licensing.get("license_analysis", {})
        compliance = licensing.get("compliance_score", {})
        if licensing:
            spdx_id = analysis.get("spdx_id") or ""
            if spdx_id in ("NOASSERTION", ""):
                spdx_id = None
            license_name = spdx_id or analysis.get("license_type") or "Unknown"
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
                f'<p class="sub-detail">Category: {analysis.get("category", "Unknown")}</p>',
            ]

            # 2. Automated FAIR4RS Assessment — not yet collected
            lic_lines.append('<p><strong>Automated FAIR4RS Assessment:</strong> Not yet collected</p>')

            # 3. OSI License Validation — passes if osi_approved is True
            osi = analysis.get("osi_approved")
            osi_label = "Yes" if osi is True else ("No" if osi is False else "Unknown")
            osi_passing = osi is True
            lic_pts += 1 if osi_passing else 0
            lic_lines.append(
                f'<p><strong>OSI License Validation:</strong> {osi_label} {"✓" if osi_passing else "✗"}</p>'
            )

            # 4–5. Not yet collected
            lic_lines.append('<p><strong>License Exception Handling:</strong> Not yet collected</p>')
            lic_lines.append('<p><strong>FAIR Metadata Assessment:</strong> Not yet collected</p>')

            lic_lines.append(f'<p><strong>Score:</strong> {lic_pts}/5</p>')
            section_422_data = "\n".join(lic_lines)
        else:
            section_422_data = None

        # --- 4.2.3 Active Maintenance (PDF §4.2.3 — 6 sub-metrics) ---
        # 1. Commit Activity Pattern Analysis   2. Maintenance Mode Indicator Detection
        # 3. Activity Trend Monitoring          4. Release Pattern Assessment
        # 5. Multi-Channel Communication        6. Contributor Abandonment Forecasting
        maintenance = sust.get("maintenance", {})
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
            trend_ok = trend in ("stable", "increasing")
            maint_pts += 1 if trend_ok else 0
            maint_lines.append(f'<p><strong>Activity Trend Monitoring:</strong> '
                                f'{trend.capitalize()} {"✓" if trend_ok else "✗"}</p>')

            # 4. Release Pattern Assessment
            rel_count = releases.get("releases_last_year", 0)
            rel_ok = rel_count >= 1
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

            # 5–6. Not yet collected
            maint_lines.append('<p><strong>Multi-Channel Communication Activity:</strong> Not yet collected</p>')
            maint_lines.append('<p><strong>Contributor Abandonment Forecasting:</strong> Not yet collected</p>')

            maint_lines.append(f'<p><strong>Score:</strong> {maint_pts}/6</p>')
            section_423_data = "\n".join(maint_lines) if maint_lines else None
        else:
            section_423_data = None

        # --- 4.2.4 Engagement ---
        engagement = sust.get("engagement", {})
        if engagement:
            eng_score = engagement.get("overall_score", {})
            sub = eng_score.get("sub_scores", {})
            eng_lines = []

            def _fmt_sub(key: str) -> str:
                s = sub.get(key, {})
                label = s.get("label", key)
                if s.get("not_collected"):
                    return f'<p><strong>{label}:</strong> Not yet collected</p>'
                val = s.get("value", "N/A")
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
                rel_last_year >= 1,
                active_weeks >= 26,   # committed in at least half the weeks of the year
            ])
            activity_ok = dims_active >= 2
            long_pts += 1 if activity_ok else 0
            section_4210_lines.append(
                f'<p><strong>Comprehensive Activity Analysis:</strong> '
                f'{dims_active}/3 dimensions active {"✓" if activity_ok else "✗"}</p>'
            )
            section_4210_lines.append(
                f'<p class="sub-detail">{commits_52w:,} commits and {rel_last_year} '
                f'release(s) in the last year; active in {active_weeks}/52 weeks</p>'
            )

            # 2. Contributor Viability Assessment — bus factor. Threshold (>= 3) matches
            #    active_maintenance.py and 4.3.6 so the same number can't read healthy
            #    in one section and at-risk in another.
            bus_factor = contribs.get("bus_factor", 0)
            total_contribs = contribs.get("total_contributors", 0)
            viable = bus_factor >= 3
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
            if days_since_push is not None and days_since_push > 365:
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
            trend_ok = trend in ("stable", "increasing")
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
            lifecycle_ok = stage in ("Growing", "Mature")
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

        def _sub_row(sub: Dict, key: str) -> str:
            """Render one collector sub-score as a section row (plus any detail)."""
            info = sub.get(key, {})
            label = info.get("label", key)
            if info.get("not_collected"):
                return f'<p><strong>{label}:</strong> Not yet collected</p>'
            mark = "✓" if info.get("passing") else "✗"
            row = f'<p><strong>{label}:</strong> {info.get("value", "N/A")} {mark}</p>'
            if info.get("detail"):
                row += f'\n<p class="sub-detail">{info["detail"]}</p>'
            return row

        # --- 4.2.5 Outreach (PDF §4.2.5 — 8 sub-metrics) ---
        outreach = sust.get("outreach", {})
        if outreach:
            osub = outreach.get("overall_score", {}).get("sub_scores", {})
            out_lines = [
                _sub_row(osub, key) for key in [
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
            out_lines.append(
                f'<p><strong>Score:</strong> {outreach.get("overall_score", {}).get("score", 0)}/8</p>'
            )
            section_425_data = "\n".join(out_lines)
        else:
            section_425_data = None

        # --- 4.2.6 Welcomeness (PDF §4.2.6 — 7 sub-metrics) ---
        # Only Decision-Making Visibility is answerable from repository data;
        # the other six need sentiment/tone analysis or maintainer demographics.
        welcomeness = sust.get("welcomeness", {})
        if welcomeness:
            wsub = welcomeness.get("overall_score", {}).get("sub_scores", {})
            wel_lines = [
                _sub_row(wsub, key) for key in [
                    "chaoss_community_experience",
                    "response_quality_tone",
                    "communication_sentiment",
                    "contributor_journey",
                    "language_communication",
                    "leadership_representation",
                    "decision_making_visibility",
                ]
            ]
            wel_lines.append(
                f'<p><strong>Score:</strong> {welcomeness.get("overall_score", {}).get("score", 0)}/7</p>'
            )
            section_426_data = "\n".join(wel_lines)
        else:
            section_426_data = None

        # --- 4.2.8 Financial Sustainability / 4.2.9 Institutional Support ---
        # One collector serves both: they rest on the same funding documentation
        # and contributor-affiliation pass.
        funding = sust.get("funding", {})
        if funding:
            fsub = funding.get("overall_score", {}).get("sub_scores", {})
            fin_lines = [
                _sub_row(fsub, key) for key in [
                    "funding_documentation",
                    "institutional_affiliation",
                    "nih_r50",
                    "corporate_sponsorship",
                    "funding_portfolio",
                ]
            ]
            fin_lines.append(
                f'<p><strong>Score:</strong> {funding.get("overall_score", {}).get("score", 0)}/5</p>'
            )
            section_428_data = "\n".join(fin_lines)

            # 4.2.9 — only Institutional Support Tracking is derivable from the
            # repository. RSE titles, career development and institutional policy
            # need directory data the report itself flags as unautomatable.
            inst_row = _sub_row(fsub, "institutional_support")
            inst_pts = 1 if fsub.get("institutional_support", {}).get("passing") else 0
            section_429_data = "\n".join([
                '<p><strong>RSE Position Detection:</strong> Not yet collected</p>',
                inst_row,
                '<p><strong>Career Development Indicators:</strong> Not yet collected</p>',
                '<p><strong>NIH R50 Award Integration:</strong> Not yet collected</p>',
                '<p><strong>Institutional Policy Analysis:</strong> Not yet collected</p>',
                f'<p><strong>Score:</strong> {inst_pts}/5</p>',
            ])
        else:
            section_428_data = None
            section_429_data = None

        qual = dims.get("quality", {}).get("sub_results", {})

        # --- 4.3.1 Reliability and Robustness (PDF §4.3.1 — 5 sub-metrics) ---
        # 1. Advanced Static Analysis    2. Enhanced Security Analysis
        # 3. CERT Guidelines Compliance  4. Test Coverage Excellence
        # 5. Reliability Trend Analysis
        test_coverage = qual.get("test_coverage", {})
        static_analysis = qual.get("static_analysis", {})
        section_431_lines = []
        rel_pts = 0
        if test_coverage or static_analysis:
            # 1. Advanced Static Analysis — not yet collected
            section_431_lines.append('<p><strong>Advanced Static Analysis:</strong> Not yet collected</p>')

            # 2. Enhanced Security Analysis — CodeQL workflow presence
            if static_analysis:
                if static_analysis.get("has_codeql"):
                    rel_pts += 1
                    url = static_analysis.get("workflow_url", "")
                    link = f'<a href="{url}">CodeQL enabled</a>' if url else "CodeQL enabled"
                    section_431_lines.append(f'<p><strong>Enhanced Security Analysis:</strong> {link} ✓</p>')
                else:
                    section_431_lines.append(
                        '<p><strong>Enhanced Security Analysis:</strong> No CodeQL workflow found ✗</p>'
                    )
            else:
                section_431_lines.append('<p><strong>Enhanced Security Analysis:</strong> Not yet collected</p>')

            # 3. CERT Guidelines Compliance — not yet collected
            section_431_lines.append('<p><strong>CERT Guidelines Compliance:</strong> Not yet collected</p>')

            # 4. Test Coverage Excellence — via Codecov public API
            if test_coverage.get("coverage_exists"):
                pct = test_coverage["coverage_percentage"]
                url = test_coverage.get("coverage_url", "")
                passing = pct >= 80
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
            else:
                section_431_lines.append(
                    '<p><strong>Test Coverage Excellence:</strong> No Codecov data found ✗</p>'
                )

            section_431_lines.append('<p><strong>Reliability Trend Analysis:</strong> Not yet collected</p>')
            section_431_lines.append(f'<p><strong>Score:</strong> {rel_pts}/5</p>')
        section_431_data = "\n".join(section_431_lines) if section_431_lines else None

        # --- 4.3.3 Reproducibility ---
        reproducibility = qual.get("reproducibility", {})
        # --- 4.3.3 Reproducibility (PDF §4.3.3 — 5 sub-metrics) ---
        # 1. FAIR4RS Compliance   2. Containerization   3. Version Control Best Practices
        # 4. Environment Management   5. Reproducibility Documentation
        if reproducibility:
            cats = reproducibility.get("categories", {})
            repr_pts = 0

            def _repr_row(label, passing, detail=None):
                nonlocal repr_pts
                repr_pts += 1 if passing else 0
                mark = "✓" if passing else "✗"
                row = f'<p><strong>{label}:</strong> {mark}</p>'
                if detail:
                    row += f'<p class="sub-detail">{detail}</p>'
                return row

            fair4rs_found = cats.get("fair4rs_metadata", {}).get("found", [])
            container_found = cats.get("containers", {}).get("found", [])
            dep_found = cats.get("dependency_pinning", {}).get("found", [])
            semver = cats.get("semantic_versioning", {})

            repr_lines = [
                _repr_row("FAIR4RS Compliance Assessment",
                          reproducibility.get("has_fair4rs_metadata"),
                          ", ".join(fair4rs_found) if fair4rs_found else None),
                _repr_row("Containerization Excellence",
                          reproducibility.get("has_container"),
                          ", ".join(container_found) if container_found else None),
                _repr_row("Version Control Best Practices",
                          reproducibility.get("uses_semantic_versioning"),
                          ", ".join(semver.get("example_tags", [])[:2]) if semver.get("example_tags") else None),
                _repr_row("Environment Management",
                          reproducibility.get("has_dependency_pinning"),
                          ", ".join(dep_found) if dep_found else None),
                _repr_row("Reproducibility Documentation",
                          reproducibility.get("has_reproducibility_docs"),
                          ", ".join(cats.get("reproducibility_docs", {}).get("found", []))
                          or None),
                f'<p><strong>Score:</strong> {repr_pts}/5</p>',
            ]
            section_433_data = "\n".join(repr_lines)
        else:
            section_433_data = None

        # --- 4.3.2 Development Practices (PDF §4.3.2 — 5 sub-metrics) ---
        # 1. CI/CD Effectiveness   2. Testing Framework   3. Code Review Quality
        # 4. Dev Tool Integration  5. Community Contribution Facilitation
        ci_cd = qual.get("ci_cd", {})
        openssf_badge = sust.get("openssf_badge", {})
        section_432_lines = []
        dp_pts = 0
        if ci_cd or openssf_badge:
            # 1. CI/CD Effectiveness Assessment
            if ci_cd:
                cicd_score = ci_cd.get("score", 0)
                cicd_max   = ci_cd.get("max_score", 6)
                passing    = cicd_score > 0
                dp_pts    += 1 if passing else 0
                mark       = "✓" if passing else "✗"
                section_432_lines.append(
                    f'<p><strong>CI/CD Effectiveness Assessment:</strong> {cicd_score}/{cicd_max} {mark}</p>'
                )
            else:
                section_432_lines.append(
                    '<p><strong>CI/CD Effectiveness Assessment:</strong> Not yet collected</p>'
                )
            # 2–4. Testing, review coverage and tooling integration
            dev_tooling = qual.get("dev_tooling", {})
            if dev_tooling:
                dsub = dev_tooling.get("overall_score", {}).get("sub_scores", {})
                for key in ["testing_framework", "code_review_quality", "dev_tool_integration"]:
                    section_432_lines.append(_sub_row(dsub, key))
                    if dsub.get(key, {}).get("passing"):
                        dp_pts += 1
            else:
                for label in [
                    "Testing Framework Excellence",
                    "Code Review Quality Analysis",
                    "Development Tool Integration",
                ]:
                    section_432_lines.append(f'<p><strong>{label}:</strong> Not yet collected</p>')
            # 5. Community Contribution Facilitation — OpenSSF badge as proxy
            if openssf_badge:
                badge_status = openssf_badge.get("badge_status", {})
                if openssf_badge.get("badge_exists"):
                    level = badge_status.get("level", "").capitalize()
                    badge_url = badge_status.get("url", "")
                    pct = badge_status.get("progress_percentage", 0)
                    passing = pct >= 100
                    dp_pts += 1 if passing else 0
                    mark = "✓" if passing else "✗"
                    link = f'<a href="{badge_url}">{level}</a>' if badge_url else level
                    section_432_lines.append(
                        f'<p><strong>Community Contribution Facilitation:</strong> OpenSSF Badge {link} ({pct:.0f}%) {mark}</p>'
                    )
                else:
                    section_432_lines.append(
                        '<p><strong>Community Contribution Facilitation:</strong> OpenSSF Badge not registered ✗</p>'
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
                    '<p><strong>Community Contribution Facilitation:</strong> Not yet collected</p>'
                )
            section_432_lines.append(f'<p><strong>Score:</strong> {dp_pts}/5</p>')
        section_432_data = "\n".join(section_432_lines) if section_432_lines else None

        # --- 4.3.5 Accessibility (PDF §4.3.5 — 5 sub-metrics) ---
        # 1. Portable Build System   2. Container Availability   3. Architecture Compatibility
        # 4. Platform Documentation  5. Deployment Environment Testing
        accessibility = qual.get("accessibility", {})
        deployment_envs = qual.get("deployment_environments", {})
        if accessibility:
            cats = accessibility.get("categories", {})
            acc_pts = 0

            def _acc_row(label, passing, detail=None):
                nonlocal acc_pts
                acc_pts += 1 if passing else 0
                mark = "✓" if passing else "✗"
                row = f'<p><strong>{label}:</strong> {mark}</p>'
                if detail:
                    row += f'<p class="sub-detail">{detail}</p>'
                return row

            build_found = cats.get("build_systems", {}).get("found", [])
            container_found = cats.get("containers", {}).get("found", [])

            # 5. Deployment Environment Testing comes from its own collector, so
            #    it is rendered with _sub_row and scored alongside the _acc_row
            #    rows before the Score line is appended.
            dep_sub = deployment_envs.get("overall_score", {}).get("sub_scores", {})
            dep_info = dep_sub.get("deployment_environment_testing", {})

            acc_lines = [
                _acc_row("Portable Build System Detection",
                         accessibility.get("has_portable_build_system"),
                         ", ".join(build_found) if build_found else None),
                _acc_row("Container Availability Assessment",
                         accessibility.get("has_container"),
                         ", ".join(container_found) if container_found else None),
                "<p><strong>Architecture Compatibility Analysis:</strong> Not yet collected</p>",
                "<p><strong>Platform Documentation Evaluation:</strong> Not yet collected</p>",
                (_sub_row(dep_sub, "deployment_environment_testing") if deployment_envs
                 else "<p><strong>Deployment Environment Testing:</strong> Not yet collected</p>"),
            ]
            if dep_info.get("passing"):
                acc_pts += 1
            acc_lines.append(f'<p><strong>Score:</strong> {acc_pts}/5</p>')
            section_435_data = "\n".join(acc_lines)
        else:
            section_435_data = None

        # --- 4.3.6 Maintainability and Understandability (PDF §4.3.6 — 5 sub-metrics) ---
        # 1. Advanced Complexity Analysis   2. Code Quality Assessment
        # 3. Documentation Quality Evaluation   4. Knowledge Distribution Analysis
        # 5. Refactoring and Evolution Tracking
        contributor_activity = maintenance.get("contributor_activity", {}) if maintenance else {}
        section_436_lines = []
        maint436_pts = 0
        if contributor_activity:
            for label in [
                "Advanced Complexity Analysis",
                "Code Quality Assessment",
                "Documentation Quality Evaluation",
            ]:
                section_436_lines.append(f'<p><strong>{label}:</strong> Not yet collected</p>')

            # 4. Knowledge Distribution Analysis — bus factor (reuses 4.2.3 data)
            # Threshold matches active_maintenance.py's own "healthy bus factor"
            # definition (>= 3), so a project isn't healthy in one section and
            # at-risk in another for the same underlying number.
            bus_factor = contributor_activity.get("bus_factor", 0)
            top_pct = contributor_activity.get("top_contributor_pct", 0)
            healthy = bus_factor >= 3
            maint436_pts += 1 if healthy else 0
            mark = "✓" if healthy else "✗"
            section_436_lines.append(
                f'<p><strong>Knowledge Distribution Analysis:</strong> Bus factor {bus_factor} {mark}</p>'
            )
            section_436_lines.append(
                f'<p class="sub-detail">Top contributor: {top_pct}% of commits '
                f'({contributor_activity.get("total_contributors", 0)} total contributors)</p>'
            )

            section_436_lines.append('<p><strong>Refactoring and Evolution Tracking:</strong> Not yet collected</p>')
            section_436_lines.append(f'<p><strong>Score:</strong> {maint436_pts}/5</p>')
        section_436_data = "\n".join(section_436_lines) if section_436_lines else None

        # Apply per-package overrides to all collected sections
        ov = pkg_overrides
        section_411_data = self._apply_section_overrides(section_411_data, ov.get("4.1.1", {}))
        section_421_data = self._apply_section_overrides(section_421_data, ov.get("4.2.1", {}))
        section_422_data = self._apply_section_overrides(section_422_data, ov.get("4.2.2", {}))
        section_423_data = self._apply_section_overrides(section_423_data, ov.get("4.2.3", {}))
        section_424_data = self._apply_section_overrides(section_424_data, ov.get("4.2.4", {}))
        section_425_data = self._apply_section_overrides(section_425_data, ov.get("4.2.5", {}))
        section_426_data = self._apply_section_overrides(section_426_data, ov.get("4.2.6", {}))
        section_428_data = self._apply_section_overrides(section_428_data, ov.get("4.2.8", {}))
        section_429_data = self._apply_section_overrides(section_429_data, ov.get("4.2.9", {}))
        section_4210_data = self._apply_section_overrides(section_4210_data, ov.get("4.2.10", {}))
        section_431_data = self._apply_section_overrides(section_431_data, ov.get("4.3.1", {}))
        section_432_data = self._apply_section_overrides(section_432_data, ov.get("4.3.2", {}))
        section_433_data = self._apply_section_overrides(section_433_data, ov.get("4.3.3", {}))
        section_435_data = self._apply_section_overrides(section_435_data, ov.get("4.3.5", {}))
        section_436_data = self._apply_section_overrides(section_436_data, ov.get("4.3.6", {}))

        return {
            "package": repo_name,
            "impact": {
                "4.1.1": {"title": "Software Citation and Adoption", "data": section_411_data},
                "4.1.2": {"title": "Field Research Impact", "data": _stub("4.1.2")},
            },
            "sustainability": {
                "4.2.1": {
                    "title": "Codes of Conduct (CoC), Governance, and Contributor Guidelines",
                    "data": section_421_data,
                },
                "4.2.2": {
                    "title": "Open-Source Licensing and FAIR Compliance",
                    "data": section_422_data,
                },
                "4.2.3": {"title": "Active Maintenance", "data": section_423_data},
                "4.2.4": {"title": "Engagement", "data": section_424_data},
                "4.2.5": {"title": "Outreach",              "data": section_425_data or _stub("4.2.5")},
                "4.2.6": {"title": "Welcomeness",            "data": section_426_data or _stub("4.2.6")},
                "4.2.7": {"title": "Collaboration",          "data": _stub("4.2.7")},
                "4.2.8": {"title": "Financial Sustainability","data": section_428_data or _stub("4.2.8")},
                "4.2.9": {"title": "Institutional & Organizational Support", "data": section_429_data or _stub("4.2.9")},
                "4.2.10": {
                    "title": "Project Longevity and Community Health",
                    "data": section_4210_data or _stub("4.2.10"),
                },
            },
            "quality": {
                "4.3.1": {"title": "Reliability and Robustness",             "data": section_431_data},
                "4.3.2": {"title": "Development Practices",                  "data": section_432_data},
                "4.3.3": {"title": "Reproducibility",                        "data": section_433_data},
                "4.3.4": {"title": "Usability",                              "data": _stub("4.3.4")},
                "4.3.5": {"title": "Accessibility",                          "data": section_435_data},
                "4.3.6": {"title": "Maintainability and Understandability",  "data": section_436_data},
                "4.3.7": {"title": "Performance and Efficiency",             "data": _stub("4.3.7")},
            },
        }

    def _write_dashboard_output(self, all_metrics: Dict):
        """Write per-package metrics.json files for the dashboard.

        Creates: output/{repo-name}-metrics/metrics.json for each package.
        These are uploaded as workflow artifacts and downloaded by the
        dashboard's update workflow into explore/github-data/.
        """
        for repo_name, metrics in all_metrics.items():
            dashboard_data = self._transform_for_dashboard(repo_name, metrics)

            # Extract repo part from "Owner/repo" for directory name
            repo_short = repo_name.split("/")[-1]
            metrics_dir = self.output_path / f"{repo_short}-metrics"
            metrics_dir.mkdir(parents=True, exist_ok=True)

            output_file = metrics_dir / "metrics.json"
            with open(output_file, "w") as f:
                json.dump(dashboard_data, f, indent=2)

            logger.info(f"Dashboard metrics written to {output_file}")


async def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Orchestrate metrics collection for dashboard"
    )
    parser.add_argument(
        "--config",
        default="config/orchestrator.yaml",
        help="Path to configuration file",
    )
    parser.add_argument("--software", help="Filter to specific software (e.g., HDF5)")
    parser.add_argument(
        "--dry-run", action="store_true", help="Perform dry run without writing outputs"
    )

    args = parser.parse_args()

    try:
        orchestrator = MetricsOrchestrator(args.config)
        metrics = await orchestrator.process_all_software(
            filter_software=args.software, dry_run=args.dry_run
        )

        logger.info(f"\n{'='*60}")
        logger.info("Orchestration complete!")
        logger.info(f"Processed {len(metrics)} packages")
        logger.info(f"{'='*60}\n")

    except Exception as e:
        logger.error(f"Orchestration failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
