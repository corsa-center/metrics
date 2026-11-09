"""
Ecosystem Dimension Collector

CASS Sustainability Metrics Report v3 - Section 4.2

The Ecosystem dimension encompasses both human and structural elements
that are crucial for ensuring long-term project resilience and continuity.

Sub-categories (4.2.1 - 4.2.10):
- 4.2.1  Codes of Conduct (CoC), Governance, and Contributor Guidelines
- 4.2.2  Open-Source Licensing and FAIR Compliance
- 4.2.3  Active Maintenance
- 4.2.4  Engagement
- 4.2.5  Outreach
- 4.2.6  Welcomeness
- 4.2.7  Collaboration
- 4.2.8  Financial Sustainability
- 4.2.9  Institutional & Organizational Support
- 4.2.10 Project Longevity and Community Health
"""

from typing import Dict, Optional

from collectors.dimension import DimensionCollector, SubCollector, percentage_at


def _engagement_score(result: Dict) -> Optional[float]:
    """Engagement reports points out of a maximum rather than a percentage."""
    overall = result.get("overall_score", {})
    max_score = overall.get("max_score", 7)
    score = overall.get("score")
    if max_score and isinstance(score, (int, float)):
        return round(score / max_score * 100)
    return None


def _scorecard_score(result: Dict) -> Optional[float]:
    return result.get("percentage")


class EcosystemDimensionCollector(DimensionCollector):
    """Collector for Ecosystem dimension metrics (CASS Report Section 4.2)"""

    dimension = "ecosystem"
    SUB_COLLECTORS = (
        # 4.2.1 CoC, Governance, and Contributor Guidelines
        SubCollector("community_health", "governance", "collectors.ecosystem.community_health",
                     "CommunityHealthCollector", "Governance",
                     percentage_at("overall_score", "percentage")),
        # 4.2.2 Licensing
        SubCollector("licensing", "licensing", "collectors.ecosystem.licensing",
                     "LicensingCollector", "Licensing",
                     percentage_at("compliance_score", "percentage")),
        # 4.2.3 Active Maintenance
        SubCollector("active_maintenance", "maintenance", "collectors.ecosystem.active_maintenance",
                     "ActiveMaintenanceCollector", "Active maintenance",
                     percentage_at("score", "percentage")),
        # 4.2.4 CHAOSS Activity Metrics
        SubCollector("chaoss_activity", "chaoss_activity", "collectors.ecosystem.chaoss_governance",
                     "CHAOSSGovernanceCollector", "CHAOSS activity",
                     percentage_at("overall_score", "score")),
        # 4.2.5 OpenSSF Best Practices Badge
        SubCollector("openssf_badge", "openssf_badge", "collectors.ecosystem.openssf_badge",
                     "OpenSSFBadgeCollector", "OpenSSF badge",
                     percentage_at("overall_score", "score")),
        # 4.2.4 Engagement -- issue/PR response times, open/close ratios
        SubCollector("engagement", "engagement", "collectors.ecosystem.engagement",
                     "EngagementCollector", "Engagement", _engagement_score),
        # 4.2.2 FAIR compliance and license exceptions
        SubCollector("fair_licensing", "fair_licensing", "collectors.ecosystem.fair_licensing",
                     "FairLicensingCollector", "FAIR licensing",
                     percentage_at("overall_score", "percentage")),
        # 4.2.5 Outreach -- newcomer growth, retention, onboarding infrastructure
        SubCollector("outreach", "outreach", "collectors.ecosystem.outreach",
                     "OutreachCollector", "Outreach",
                     percentage_at("overall_score", "percentage")),
        # 4.2.6 Welcomeness -- decision-making visibility
        SubCollector("welcomeness", "welcomeness", "collectors.ecosystem.welcomeness",
                     "WelcomenessCollector", "Welcomeness",
                     percentage_at("overall_score", "percentage")),
        # 4.2.7 Collaboration -- ecosystem reach via ecosyste.ms
        SubCollector("collaboration", "collaboration", "collectors.ecosystem.collaboration",
                     "CollaborationCollector", "Collaboration",
                     percentage_at("overall_score", "percentage")),
        # 4.2.8 + 4.2.9 Funding and institutional affiliation
        SubCollector("funding", "funding", "collectors.ecosystem.funding",
                     "FundingCollector", "Funding",
                     percentage_at("overall_score", "percentage")),
        # OpenSSF Scorecard
        SubCollector("openssf_scorecard", "openssf_scorecard", "collectors.ecosystem.openssf_scorecard",
                     "OpenSSFScorecardCollector", "OpenSSF Scorecard", _scorecard_score),
    )
