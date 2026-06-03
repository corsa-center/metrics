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
        try:
            from collectors.sustainability.community_health import CommunityHealthCollector
            collector = CommunityHealthCollector(github_token=github_token)
            sub_results["governance"] = await collector.collect(package)
        except Exception as e:
            logger.warning(f"Governance collection failed for {package['name']}: {e}")

        # 4.2.2 Licensing and FAIR Compliance
        try:
            from collectors.sustainability.licensing import LicensingCollector
            collector = LicensingCollector(github_token=github_token)
            sub_results["licensing"] = await collector.collect(package)
        except Exception as e:
            logger.warning(f"Licensing collection failed for {package['name']}: {e}")

        # 4.2.3 Active Maintenance
        try:
            from collectors.sustainability.active_maintenance import ActiveMaintenanceCollector
            collector = ActiveMaintenanceCollector(github_token=github_token)
            sub_results["maintenance"] = await collector.collect(package)
        except Exception as e:
            logger.warning(f"Active maintenance collection failed for {package['name']}: {e}")

        # 4.2.4 CHAOSS Activity Metrics
        try:
            from collectors.sustainability.chaoss_governance import CHAOSSGovernanceCollector
            collector = CHAOSSGovernanceCollector(github_token=github_token)
            sub_results["chaoss_activity"] = await collector.collect(package)
        except Exception as e:
            logger.warning(f"CHAOSS activity collection failed for {package['name']}: {e}")

        # 4.2.5 OpenSSF Best Practices Badge
        try:
            from collectors.sustainability.openssf_badge import OpenSSFBadgeCollector
            collector = OpenSSFBadgeCollector(github_token=github_token)
            sub_results["openssf_badge"] = await collector.collect(package)
        except Exception as e:
            logger.warning(f"OpenSSF badge collection failed for {package['name']}: {e}")

        # 4.2.4 Engagement — issue/PR response times, open/close ratios
        try:
            from collectors.sustainability.engagement import EngagementCollector
            collector = EngagementCollector(github_token=github_token)
            sub_results["engagement"] = await collector.collect(package)
        except Exception as e:
            logger.warning(f"Engagement collection failed for {package['name']}: {e}")

        # OpenSSF Scorecard
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
        try:
            from collectors.quality.development_practices.ci_cd import CICDMetricsCollector
            collector = CICDMetricsCollector(self.config)
            sub_results["ci_cd"] = await collector.collect(package)
        except Exception as e:
            logger.warning(f"CI/CD collection failed for {package['name']}: {e}")

        # 4.3.3 Reproducibility — containers, lock files, FAIR4RS metadata, semver
        try:
            from collectors.quality.reproducibility import ReproducibilityCollector
            collector = ReproducibilityCollector(github_token=github_token)
            sub_results["reproducibility"] = await collector.collect(package)
        except Exception as e:
            logger.warning(f"Reproducibility collection failed for {package['name']}: {e}")

        # 4.3.5 Accessibility — portable build systems and containers
        try:
            from collectors.quality.accessibility import AccessibilityCollector
            collector = AccessibilityCollector(github_token=github_token)
            sub_results["accessibility"] = await collector.collect(package)
        except Exception as e:
            logger.warning(f"Accessibility collection failed for {package['name']}: {e}")

        scores = []
        if "ci_cd" in sub_results:
            scores.append(sub_results["ci_cd"].get("percentage", 0))
        if "reproducibility" in sub_results:
            scores.append(sub_results["reproducibility"].get("overall_score", {}).get("percentage", 0))
        if "accessibility" in sub_results:
            scores.append(sub_results["accessibility"].get("overall_score", {}).get("percentage", 0))

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
            score = impact_sub.get("score", 0)
            citation_lines.append(f'<p><strong>Citation Score:</strong> {score:.1f}/100</p>')
            section_411_data = "\n".join(citation_lines) if citation_lines else None
        else:
            section_411_data = None

        sust = dims.get("sustainability", {}).get("sub_results", {})

        # --- 4.2.1 CoC, Governance, and Contributor Guidelines ---
        governance = sust.get("governance", {})
        gov_lines = []
        for label, key in [
            ("Code of Conduct", "code_of_conduct"),
            ("Governance", "governance"),
            ("Contributing Guidelines", "contributing_guidelines"),
        ]:
            info = governance.get(key, {})
            if info.get("exists"):
                url = info.get("url", "")
                link = f'<a href="{url}">{info.get("file_path", "")}</a>' if url else info.get("file_path", "")
                gov_lines.append(f"<p><strong>{label}:</strong> {link}</p>")
            else:
                gov_lines.append(f"<p><strong>{label}:</strong> Not found</p>")
        gov_score = governance.get("overall_score", {})
        if gov_score:
            gov_lines.append(
                f'<p><strong>Score:</strong> {gov_score.get("score", 0)}/{gov_score.get("max_score", 3)}</p>'
            )
        scorecard = sust.get("openssf_scorecard", {})
        if scorecard and scorecard.get("scorecard_exists"):
            score_val = scorecard.get("score")
            sc_url = scorecard.get("scorecard_url", "")
            checks_label = f'{scorecard.get("checks_passed", 0)}/{scorecard.get("checks_total", 0)} checks passed'
            if sc_url and score_val is not None:
                gov_lines.append(
                    f'<p><strong>OpenSSF Scorecard:</strong> <a href="{sc_url}">{score_val}/10</a> ({checks_label})</p>'
                )
            elif score_val is not None:
                gov_lines.append(
                    f'<p><strong>OpenSSF Scorecard:</strong> {score_val}/10 ({checks_label})</p>'
                )
        elif scorecard and not scorecard.get("scorecard_exists") and scorecard.get("recommendation"):
            gov_lines.append('<p><strong>OpenSSF Scorecard:</strong> Not available</p>')
        section_421_data = "\n".join(gov_lines) if governance else None

        # --- 4.2.2 Licensing and FAIR Compliance ---
        licensing = sust.get("licensing", {})
        analysis = licensing.get("license_analysis", {})
        compliance = licensing.get("compliance_score", {})
        if licensing:
            spdx_id = analysis.get("spdx_id") or ""
            if spdx_id in ("NOASSERTION", ""):
                spdx_id = None
            license_name = spdx_id or analysis.get("license_type") or "Unknown"
            lic_lines = [
                f"<p><strong>License:</strong> {license_name}</p>",
                f"<p><strong>Category:</strong> {analysis.get('category', 'Unknown')}</p>",
                f"<p><strong>OSI Approved:</strong> {'Yes' if analysis.get('osi_approved') else 'No' if analysis.get('osi_approved') is False else 'Unknown'}</p>",
                f"<p><strong>Compliance Score:</strong> {compliance.get('score', 0)}/{compliance.get('max_score', 3)} ({compliance.get('percentage', 0):.0f}%)</p>",
            ]
            # Include detail checklist
            for detail in compliance.get("details", []):
                lic_lines.append(f"<p>{detail}</p>")
            section_422_data = "\n".join(lic_lines)
        else:
            section_422_data = None

        # --- 4.2.3 Active Maintenance ---
        maintenance = sust.get("maintenance", {})
        if maintenance:
            maint_lines = []
            # Maintenance indicators
            indicators = maintenance.get("maintenance_indicators", {})
            if indicators.get("archived"):
                maint_lines.append("<p><strong>Status:</strong> Archived</p>")
            elif indicators.get("maintenance_signals"):
                signals = ", ".join(indicators["maintenance_signals"])
                maint_lines.append(f"<p><strong>Status:</strong> {signals}</p>")
            else:
                maint_lines.append("<p><strong>Status:</strong> Active</p>")

            # Commit activity
            commits = maintenance.get("commit_activity", {})
            if commits.get("days_since_last_commit") is not None:
                maint_lines.append(
                    f'<p><strong>Last Commit:</strong> {commits["days_since_last_commit"]} days ago</p>'
                )
            if commits.get("total_commits_52w"):
                maint_lines.append(
                    f'<p><strong>Commits (52 weeks):</strong> {commits["total_commits_52w"]:,} '
                    f'({commits.get("active_weeks_52w", 0)} active weeks)</p>'
                )
            if commits.get("recent_trend") and commits["recent_trend"] != "unknown":
                maint_lines.append(
                    f'<p><strong>Trend:</strong> {commits["recent_trend"].capitalize()}</p>'
                )

            # Release activity
            releases = maintenance.get("release_activity", {})
            if releases.get("latest_release"):
                maint_lines.append(
                    f'<p><strong>Latest Release:</strong> {releases["latest_release"]}'
                    f' ({releases.get("days_since_latest_release", "?")} days ago)</p>'
                )
            if releases.get("releases_last_year"):
                maint_lines.append(
                    f'<p><strong>Releases (last year):</strong> {releases["releases_last_year"]}</p>'
                )

            # Contributor activity
            contribs = maintenance.get("contributor_activity", {})
            if contribs.get("total_contributors"):
                maint_lines.append(
                    f'<p><strong>Contributors:</strong> {contribs["total_contributors"]}'
                    f' (bus factor: {contribs.get("bus_factor", 0)})</p>'
                )

            # Score
            score = maintenance.get("score", {})
            if score:
                maint_lines.append(
                    f'<p><strong>Score:</strong> {score.get("score", 0)}/{score.get("max_score", 5)}'
                    f' ({score.get("percentage", 0):.0f}%)</p>'
                )

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

            # Also surface PR cycle time as context (not a scored sub-metric)
            pr_stats = engagement.get("pr_stats", {})
            mpr_ct = pr_stats.get("median_cycle_time_hours")
            if mpr_ct is not None:
                eng_lines.append(f'<p><strong>Median PR Cycle Time:</strong> {mpr_ct:.0f} hours</p>')

            eng_lines.append(
                f'<p><strong>Score:</strong> {eng_score.get("score", 0)}/{eng_score.get("max_score", 7)}</p>'
            )
            section_424_data = "\n".join(eng_lines) if eng_lines else None
        else:
            section_424_data = None

        qual = dims.get("quality", {}).get("sub_results", {})

        # --- 4.3.3 Reproducibility ---
        reproducibility = qual.get("reproducibility", {})
        if reproducibility:
            repr_lines = [
                f'<p><strong>Container:</strong> {"Yes" if reproducibility.get("has_container") else "No"}</p>',
                f'<p><strong>Dependency Pinning:</strong> {"Yes" if reproducibility.get("has_dependency_pinning") else "No"}</p>',
                f'<p><strong>FAIR4RS Metadata:</strong> {"Yes" if reproducibility.get("has_fair4rs_metadata") else "No"}</p>',
                f'<p><strong>Semantic Versioning:</strong> {"Yes" if reproducibility.get("uses_semantic_versioning") else "No"}</p>',
            ]
            cats = reproducibility.get("categories", {})
            for cat_key, label in [
                ("containers", "Containers"),
                ("dependency_pinning", "Lock files"),
                ("fair4rs_metadata", "FAIR4RS files"),
            ]:
                found = cats.get(cat_key, {}).get("found", [])
                if found:
                    repr_lines.append(f'<p><strong>{label}:</strong> {", ".join(found)}</p>')
            semver = cats.get("semantic_versioning", {})
            if semver.get("example_tags"):
                repr_lines.append(
                    f'<p><strong>Example tags:</strong> {", ".join(semver["example_tags"])}</p>'
                )
            overall = reproducibility.get("overall_score", {})
            repr_lines.append(
                f'<p><strong>Score:</strong> {overall.get("percentage", 0):.0f}/100</p>'
            )
            section_433_data = "\n".join(repr_lines)
        else:
            section_433_data = None

        # --- 4.3.2 Development Practices ---
        ci_cd = qual.get("ci_cd", {})
        openssf_badge = sust.get("openssf_badge", {})
        section_432_lines = []
        if ci_cd:
            section_432_lines.append("<p><strong>CI/CD</strong></p>")
            section_432_lines.append(
                f'<p><strong>Score:</strong> {ci_cd.get("score", 0)}/{ci_cd.get("max_score", 6)}'
                f' ({ci_cd.get("percentage", 0):.0f}%)</p>'
            )
            for key, val in ci_cd.get("details", []):
                if key == "total_workflow_success_percentage" and val:
                    section_432_lines.append(
                        f'<p><strong>Workflow Success Rate:</strong> {val:.0f}%</p>'
                    )
        if openssf_badge:
            section_432_lines.append("<p><strong>OpenSSF Best Practices Badge</strong></p>")
            badge_status = openssf_badge.get("badge_status", {})
            if openssf_badge.get("badge_exists"):
                level = badge_status.get("level", "").capitalize()
                badge_url = badge_status.get("url", "")
                pct = badge_status.get("progress_percentage", 0)
                if badge_url:
                    section_432_lines.append(
                        f'<p><strong>Badge:</strong> <a href="{badge_url}">{level}</a> ({pct:.0f}%)</p>'
                    )
                else:
                    section_432_lines.append(f'<p><strong>Badge:</strong> {level} ({pct:.0f}%)</p>')
            else:
                section_432_lines.append('<p><strong>Badge:</strong> Not registered</p>')
                overall = openssf_badge.get("overall_score", {})
                if openssf_badge.get("assessment_method") == "repository_scan" and overall.get("percentage"):
                    section_432_lines.append(
                        f'<p><strong>Estimated readiness:</strong> {overall["percentage"]:.0f}%</p>'
                    )
                if openssf_badge.get("recommendation"):
                    section_432_lines.append(f'<p>{openssf_badge["recommendation"]}</p>')
        section_432_data = "\n".join(section_432_lines) if section_432_lines else None

        # --- 4.3.5 Accessibility ---
        accessibility = qual.get("accessibility", {})
        if accessibility:
            acc_lines = [
                f'<p><strong>Container:</strong> {"Yes" if accessibility.get("has_container") else "No"}</p>',
                f'<p><strong>Portable Build System:</strong> {"Yes" if accessibility.get("has_portable_build_system") else "No"}</p>',
            ]
            cats = accessibility.get("categories", {})
            for cat_key, label in [
                ("containers", "Containers"),
                ("build_systems", "Build systems"),
                ("python_packaging", "Python packaging"),
            ]:
                found = cats.get(cat_key, {}).get("found", [])
                if found:
                    acc_lines.append(f'<p><strong>{label}:</strong> {", ".join(found)}</p>')
            overall = accessibility.get("overall_score", {})
            acc_lines.append(
                f'<p><strong>Score:</strong> {overall.get("score", 0)}/{overall.get("max_score", 0)}'
                f' ({overall.get("percentage", 0):.0f}%)</p>'
            )
            section_435_data = "\n".join(acc_lines)
        else:
            section_435_data = None

        return {
            "package": repo_name,
            "impact": {
                "4.1.1": {"title": "Software Citation and Adoption", "data": section_411_data},
                "4.1.2": {"title": "Field Research Impact", "data": None},
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
                "4.2.5": {"title": "Outreach", "data": None},
                "4.2.6": {"title": "Welcomeness", "data": None},
                "4.2.7": {"title": "Collaboration", "data": None},
                "4.2.8": {"title": "Financial Sustainability", "data": None},
                "4.2.9": {"title": "Institutional & Organizational Support", "data": None},
                "4.2.10": {"title": "Project Longevity and Community Health", "data": None},
            },
            "quality": {
                "4.3.1": {"title": "Reliability and Robustness", "data": None},
                "4.3.2": {"title": "Development Practices", "data": section_432_data},
                "4.3.3": {"title": "Reproducibility", "data": section_433_data},
                "4.3.4": {"title": "Usability", "data": None},
                "4.3.5": {"title": "Accessibility", "data": section_435_data},
                "4.3.6": {"title": "Maintainability and Understandability", "data": None},
                "4.3.7": {"title": "Performance and Efficiency", "data": None},
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
