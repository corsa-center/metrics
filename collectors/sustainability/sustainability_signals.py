"""
Sustainability Signals Collector

Detects warning indicators that a project may be unmaintained or at risk:
- Deprecation notices in README and documentation
- GitHub archive/disabled status
- Unmaintained/abandoned announcements
- Successor project references
- Positive counter-signals (funding, governance)

This collector focuses on WARNING DETECTION only.
For activity metrics, use development_activity.py.
For contributor metrics, use development_activity.py.
"""

import asyncio
import logging
import re
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timedelta
from collections import defaultdict
import httpx

logger = logging.getLogger(__name__)


class SustainabilitySignalsCollector:
    """
    Collects sustainability warning signals by analyzing repository
    status, activity patterns, and maintenance indicators.
    """

    # Maintenance status patterns in README and other files
    MAINTENANCE_PATTERNS = {
        "deprecated": {
            "patterns": [
                r"\b(deprecated|deprecation)\b",
                r"this\s+(project|package|library|repo)\s+is\s+deprecated",
                r"no\s+longer\s+(maintained|supported|developed)",
                r"please\s+use\s+.+\s+instead",
                r"superseded\s+by",
                r"replaced\s+by",
                r"migrated?\s+to",
            ],
            "severity": "critical",
        },
        "unmaintained": {
            "patterns": [
                r"\b(unmaintained|not\s+maintained)\b",
                r"looking\s+for\s+(maintainer|new\s+owner)",
                r"seeking\s+(maintainer|contributors)",
                r"needs?\s+(new\s+)?maintainer",
                r"orphaned?\s+project",
                r"maintenance\s+mode",
                r"minimal\s+maintenance",
            ],
            "severity": "high",
        },
        "archived": {
            "patterns": [
                r"\b(archived|read-only)\b",
                r"this\s+repo(sitory)?\s+is\s+archived",
                r"no\s+(new\s+)?issues\s+(will\s+be\s+)?accepted",
                r"pull\s+requests?\s+are\s+closed",
            ],
            "severity": "critical",
        },
        "experimental": {
            "patterns": [
                r"\b(experimental|alpha|beta|pre-release|unstable)\b",
                r"not\s+(ready\s+)?for\s+production",
                r"use\s+at\s+your\s+own\s+risk",
                r"work\s+in\s+progress",
                r"proof\s+of\s+concept",
            ],
            "severity": "medium",
        },
        "limited_support": {
            "patterns": [
                r"limited\s+support",
                r"best\s+effort\s+(support|maintenance)",
                r"security\s+(fixes|patches)\s+only",
                r"critical\s+(fixes|patches)\s+only",
                r"bug\s+fixes\s+only",
            ],
            "severity": "medium",
        },
    }

    # Badges that indicate sustainability concerns
    WARNING_BADGES = {
        "deprecated": [
            r"badge.*deprecated",
            r"deprecated.*badge",
            r"maintenance.*deprecated",
        ],
        "unmaintained": [
            r"badge.*unmaintained",
            r"unmaintained.*badge",
            r"maintenance.*none",
            r"no.*maintenance",
        ],
        "archived": [
            r"badge.*archived",
            r"archived.*badge",
        ],
    }

    # Positive sustainability indicators
    POSITIVE_INDICATORS = {
        "funding": [
            r"FUNDING\.yml",
            r"sponsors?",
            r"opencollective",
            r"patreon",
            r"github\s+sponsors",
            r"buy\s+me\s+a\s+coffee",
            r"liberapay",
            r"ko-fi",
        ],
        "governance": [
            r"GOVERNANCE\.md",
            r"steering\s+committee",
            r"technical\s+committee",
            r"core\s+team",
            r"maintainers\.md",
        ],
        "roadmap": [
            r"ROADMAP\.md",
            r"roadmap",
            r"milestones?",
            r"future\s+plans",
        ],
        "active_development": [
            r"actively\s+maintained",
            r"active\s+development",
            r"accepting\s+(pull\s+requests|contributions|PRs)",
            r"contributions?\s+welcome",
        ],
    }

    def __init__(self, github_token: Optional[str] = None):
        """Initialize the collector with optional GitHub token."""
        self.github_token = github_token
        self.github_headers = {}
        if github_token:
            self.github_headers = {
                "Authorization": f"token {github_token}",
                "Accept": "application/vnd.github.v3+json",
            }

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        """
        Main entry point - collects sustainability warning signals.

        Args:
            package: Dictionary containing package info with 'repo_url' key

        Returns:
            Dictionary with all collected sustainability signals
        """
        repo_url = package.get("repo_url", "")
        repo_name = package.get("name", "unknown")

        logger.info(f"Collecting sustainability signals for {repo_name}")

        owner_repo = self._extract_owner_repo(repo_url)
        if not owner_repo:
            logger.warning(f"Could not extract owner/repo from URL: {repo_url}")
            return self._empty_result(repo_name)

        owner, repo = owner_repo

        try:
            # Collect warning signals concurrently
            results = await asyncio.gather(
                self._check_maintenance_status(owner, repo),
                self._detect_sustainability_indicators(owner, repo),
                return_exceptions=True,
            )

            # Handle exceptions
            maintenance = results[0] if not isinstance(results[0], Exception) else self._empty_maintenance()
            indicators = results[1] if not isinstance(results[1], Exception) else self._empty_indicators()

            # Log exceptions
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"Error in sustainability analysis {i}: {result}")

            # Calculate overall risk score
            overall_score = self._calculate_overall_score(maintenance, indicators)

            # Generate warnings summary
            warnings = self._generate_warnings(maintenance)

            return {
                "package_name": repo_name,
                "repository": f"{owner}/{repo}",
                "timestamp": self._get_timestamp(),
                "maintenance_status": maintenance,
                "sustainability_indicators": indicators,
                "overall_score": overall_score,
                "warnings": warnings,
            }

        except Exception as e:
            logger.error(f"Error collecting sustainability signals for {repo_name}: {e}")
            return self._empty_result(repo_name)

    # ==================== Maintenance Status Detection ====================

    async def _check_maintenance_status(self, owner: str, repo: str) -> Dict[str, Any]:
        """
        Check for maintenance status indicators.

        Detects:
        - GitHub archive status
        - Deprecation notices in README
        - Maintenance mode announcements
        - Successor project references
        """
        logger.debug(f"Checking maintenance status for {owner}/{repo}")

        try:
            # Fetch repository info and README concurrently
            results = await asyncio.gather(
                self._fetch_repo_info(owner, repo),
                self._fetch_readme(owner, repo),
                self._fetch_file_content(owner, repo, ".github/README.md"),
                return_exceptions=True,
            )

            repo_info = results[0] if not isinstance(results[0], Exception) else {}
            readme_content = results[1] if not isinstance(results[1], Exception) else ""
            github_readme = results[2] if not isinstance(results[2], Exception) else ""

            # Combine README content
            full_readme = f"{readme_content}\n{github_readme}"

            # Check GitHub archive status
            is_archived = repo_info.get("archived", False)
            is_disabled = repo_info.get("disabled", False)

            # Analyze README for maintenance patterns
            detected_patterns: Dict[str, List[Dict]] = defaultdict(list)
            readme_lower = full_readme.lower()

            for status_type, config in self.MAINTENANCE_PATTERNS.items():
                for pattern in config["patterns"]:
                    matches = re.finditer(pattern, readme_lower, re.IGNORECASE | re.MULTILINE)
                    for match in matches:
                        # Get surrounding context
                        start = max(0, match.start() - 50)
                        end = min(len(full_readme), match.end() + 50)
                        context = full_readme[start:end].strip()

                        detected_patterns[status_type].append({
                            "match": match.group(),
                            "context": context,
                            "severity": config["severity"],
                        })

            # Check for warning badges
            badge_warnings = []
            for badge_type, patterns in self.WARNING_BADGES.items():
                for pattern in patterns:
                    if re.search(pattern, readme_lower):
                        badge_warnings.append(badge_type)
                        break

            # Determine overall maintenance status
            status = "active"
            severity = "none"

            if is_archived:
                status = "archived"
                severity = "critical"
            elif is_disabled:
                status = "disabled"
                severity = "critical"
            elif detected_patterns.get("deprecated"):
                status = "deprecated"
                severity = "critical"
            elif detected_patterns.get("unmaintained"):
                status = "unmaintained"
                severity = "high"
            elif detected_patterns.get("archived"):
                status = "archived_notice"
                severity = "critical"
            elif detected_patterns.get("limited_support"):
                status = "limited_support"
                severity = "medium"
            elif detected_patterns.get("experimental"):
                status = "experimental"
                severity = "low"

            # Check for successor project
            successor = self._find_successor_project(full_readme)

            # Calculate score (higher = healthier)
            score_map = {
                "none": 100,
                "low": 80,
                "medium": 60,
                "high": 30,
                "critical": 0,
            }
            score = score_map.get(severity, 50)

            return {
                "score": score,
                "status": status,
                "severity": severity,
                "is_archived": is_archived,
                "is_disabled": is_disabled,
                "detected_warnings": {
                    k: len(v) for k, v in detected_patterns.items() if v
                },
                "warning_details": {
                    k: v[:3] for k, v in detected_patterns.items() if v
                },
                "badge_warnings": badge_warnings,
                "successor_project": successor,
                "repo_description": repo_info.get("description", ""),
                "last_push": repo_info.get("pushed_at"),
                "created_at": repo_info.get("created_at"),
            }

        except Exception as e:
            logger.error(f"Error checking maintenance status: {e}")
            return self._empty_maintenance()

    def _find_successor_project(self, content: str) -> Optional[Dict[str, str]]:
        """Find references to successor/replacement projects."""
        patterns = [
            r"(?:please\s+use|replaced\s+by|superseded\s+by|migrated?\s+to|see|use)\s+[`\[]?([a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+)[`\]]?",
            r"github\.com/([a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+)",
        ]

        for pattern in patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                successor = match.group(1)
                # Don't return self-references
                if "/" in successor:
                    return {
                        "repository": successor,
                        "url": f"https://github.com/{successor}",
                    }

        return None

    # ==================== Sustainability Indicators Detection ====================

    async def _detect_sustainability_indicators(self, owner: str, repo: str) -> Dict[str, Any]:
        """
        Detect positive sustainability indicators.

        Checks for:
        - Funding/sponsorship setup
        - Governance documentation
        - Roadmap and planning
        - Active development signals
        - Organization backing
        """
        logger.debug(f"Detecting sustainability indicators for {owner}/{repo}")

        try:
            # Fetch repository info and file tree
            results = await asyncio.gather(
                self._fetch_repo_info(owner, repo),
                self._fetch_repo_tree(owner, repo),
                self._fetch_readme(owner, repo),
                self._check_funding_file(owner, repo),
                return_exceptions=True,
            )

            repo_info = results[0] if not isinstance(results[0], Exception) else {}
            tree = results[1] if not isinstance(results[1], Exception) else []
            readme = results[2] if not isinstance(results[2], Exception) else ""
            funding = results[3] if not isinstance(results[3], Exception) else {}

            all_paths = [item.get("path", "") for item in tree] if tree else []
            all_paths_lower = [p.lower() for p in all_paths]

            # Detect indicators
            indicators = {
                "funding": {
                    "has_funding_file": funding.get("exists", False),
                    "funding_platforms": funding.get("platforms", []),
                    "has_sponsors": repo_info.get("has_sponsors", False) or "sponsor" in readme.lower(),
                },
                "governance": {
                    "has_governance_file": any("governance" in p for p in all_paths_lower),
                    "has_maintainers_file": any("maintainer" in p for p in all_paths_lower),
                    "has_codeowners": any("codeowners" in p for p in all_paths_lower),
                    "mentions_committee": bool(re.search(r"(steering|technical)\s+committee", readme, re.IGNORECASE)),
                },
                "planning": {
                    "has_roadmap": any("roadmap" in p for p in all_paths_lower),
                    "has_milestones": False,  # Would need separate API call
                    "has_projects": repo_info.get("has_projects", False),
                },
                "organization": {
                    "is_org_repo": "/" in repo_info.get("full_name", ""),
                    "org_name": owner,
                    "has_org_profile": False,  # Would need separate check
                },
                "community": {
                    "has_discussions": repo_info.get("has_discussions", False),
                    "has_wiki": repo_info.get("has_wiki", False),
                    "has_contributing": any("contributing" in p for p in all_paths_lower),
                    "has_code_of_conduct": any("code_of_conduct" in p for p in all_paths_lower),
                },
            }

            # Check for active development signals in README
            active_signals = []
            for indicator_type, patterns in self.POSITIVE_INDICATORS.items():
                for pattern in patterns:
                    if re.search(pattern, readme, re.IGNORECASE):
                        active_signals.append(indicator_type)
                        break

            indicators["active_signals"] = list(set(active_signals))

            # Calculate sustainability score
            score = 50  # Base score

            # Funding (+20 max)
            if indicators["funding"]["has_funding_file"]:
                score += 10
            if indicators["funding"]["has_sponsors"]:
                score += 10

            # Governance (+15 max)
            if indicators["governance"]["has_governance_file"]:
                score += 10
            if indicators["governance"]["has_codeowners"]:
                score += 5

            # Planning (+10 max)
            if indicators["planning"]["has_roadmap"]:
                score += 5
            if indicators["planning"]["has_projects"]:
                score += 5

            # Community (+15 max)
            if indicators["community"]["has_discussions"]:
                score += 5
            if indicators["community"]["has_contributing"]:
                score += 5
            if indicators["community"]["has_code_of_conduct"]:
                score += 5

            # Active signals (+10 max)
            score += min(10, len(active_signals) * 3)

            score = min(100, score)

            return {
                "score": score,
                "indicators": indicators,
                "active_signals": active_signals,
                "positive_count": sum([
                    indicators["funding"]["has_funding_file"],
                    indicators["funding"]["has_sponsors"],
                    indicators["governance"]["has_governance_file"],
                    indicators["governance"]["has_codeowners"],
                    indicators["planning"]["has_roadmap"],
                    indicators["community"]["has_discussions"],
                    indicators["community"]["has_contributing"],
                    indicators["community"]["has_code_of_conduct"],
                ]),
                "status": self._get_indicator_status(score),
            }

        except Exception as e:
            logger.error(f"Error detecting sustainability indicators: {e}")
            return self._empty_indicators()

    async def _check_funding_file(self, owner: str, repo: str) -> Dict[str, Any]:
        """Check for FUNDING.yml file and parse contents."""
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/.github/FUNDING.yml"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, headers=self.github_headers)
                if response.status_code == 200:
                    data = response.json()
                    # Parse funding platforms
                    platforms = []
                    if data.get("encoding") == "base64":
                        import base64
                        content = base64.b64decode(data.get("content", "")).decode("utf-8")
                        for platform in ["github", "patreon", "open_collective", "ko_fi", "tidelift",
                                        "community_bridge", "liberapay", "issuehunt", "otechie"]:
                            if platform in content.lower():
                                platforms.append(platform)

                    return {"exists": True, "platforms": platforms}
        except Exception as e:
            logger.debug(f"Error checking funding file: {e}")

        return {"exists": False, "platforms": []}

    # ==================== Helper Methods ====================

    async def _fetch_repo_info(self, owner: str, repo: str) -> Dict:
        """Fetch repository information."""
        url = f"https://api.github.com/repos/{owner}/{repo}"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=self.github_headers)
                if response.status_code == 200:
                    return response.json()
        except Exception as e:
            logger.debug(f"Error fetching repo info: {e}")

        return {}

    async def _fetch_readme(self, owner: str, repo: str) -> str:
        """Fetch README content."""
        url = f"https://api.github.com/repos/{owner}/{repo}/readme"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                headers = {**self.github_headers, "Accept": "application/vnd.github.raw"}
                response = await client.get(url, headers=headers)
                if response.status_code == 200:
                    return response.text
        except Exception as e:
            logger.debug(f"Error fetching README: {e}")

        return ""

    async def _fetch_file_content(self, owner: str, repo: str, path: str) -> str:
        """Fetch file content."""
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, headers=self.github_headers)
                if response.status_code == 200:
                    data = response.json()
                    if data.get("encoding") == "base64":
                        import base64
                        return base64.b64decode(data.get("content", "")).decode("utf-8")
        except Exception as e:
            logger.debug(f"Error fetching file {path}: {e}")

        return ""

    async def _fetch_repo_tree(self, owner: str, repo: str) -> List[Dict]:
        """Fetch repository file tree."""
        url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/HEAD?recursive=1"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=self.github_headers)
                if response.status_code == 200:
                    return response.json().get("tree", [])
        except Exception as e:
            logger.debug(f"Error fetching repo tree: {e}")

        return []

    def _extract_owner_repo(self, repo_url: str) -> Optional[Tuple[str, str]]:
        """Extract owner and repo from GitHub URL."""
        patterns = [
            r"github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$",
            r"github\.com:([^/]+)/([^/]+?)(?:\.git)?/?$",
        ]
        for pattern in patterns:
            match = re.search(pattern, repo_url)
            if match:
                owner = match.group(1)
                repo = match.group(2).replace(".git", "")
                return (owner, repo)
        return None

    def _calculate_overall_score(
        self,
        maintenance: Dict[str, Any],
        indicators: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Calculate overall sustainability warning score."""
        maintenance_score = maintenance.get("score", 50)
        indicators_score = indicators.get("score", 50)

        # Maintenance status is most important for warnings
        weighted_score = maintenance_score * 0.7 + indicators_score * 0.3

        # Determine risk level
        if weighted_score >= 80:
            status = "low_risk"
        elif weighted_score >= 60:
            status = "moderate_risk"
        elif weighted_score >= 40:
            status = "elevated_risk"
        else:
            status = "high_risk"

        return {
            "score": round(weighted_score, 2),
            "max_score": 100,
            "status": status,
            "recommendation": self._get_recommendation(maintenance),
        }

    def _get_recommendation(self, maintenance: Dict[str, Any]) -> str:
        """Generate recommendation based on maintenance status."""
        if maintenance.get("is_archived"):
            successor = maintenance.get("successor_project")
            if successor:
                return f"Repository is archived. Consider migrating to {successor.get('repository')}."
            return "Repository is archived. Find an actively maintained alternative."

        status = maintenance.get("status", "active")
        if status == "deprecated":
            successor = maintenance.get("successor_project")
            if successor:
                return f"Project is deprecated. Consider migrating to {successor.get('repository')}."
            return "Project is deprecated. Seek an actively maintained alternative."
        elif status == "unmaintained":
            return "Project appears unmaintained. Evaluate alternatives before adoption."
        elif status == "limited_support":
            return "Limited support only. Monitor for critical updates."
        elif status == "experimental":
            return "Experimental project. Use with caution in production."

        return "No sustainability warnings detected."

    def _generate_warnings(self, maintenance: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Generate warnings list from maintenance status."""
        warnings = []

        if maintenance.get("is_archived"):
            warnings.append({
                "type": "archived",
                "message": "Repository is archived and read-only",
                "severity": "critical",
            })

        if maintenance.get("is_disabled"):
            warnings.append({
                "type": "disabled",
                "message": "Repository is disabled",
                "severity": "critical",
            })

        status = maintenance.get("status", "active")
        if status == "deprecated":
            warnings.append({
                "type": "deprecated",
                "message": "Project is marked as deprecated",
                "severity": "critical",
            })
        elif status == "unmaintained":
            warnings.append({
                "type": "unmaintained",
                "message": "Project appears to be unmaintained",
                "severity": "high",
            })
        elif status == "limited_support":
            warnings.append({
                "type": "limited_support",
                "message": "Project has limited support only",
                "severity": "medium",
            })

        # Add badge warnings
        for badge in maintenance.get("badge_warnings", []):
            warnings.append({
                "type": f"badge_{badge}",
                "message": f"Warning badge detected: {badge}",
                "severity": "high",
            })

        return warnings

    def _get_indicator_status(self, score: float) -> str:
        """Get indicator status label."""
        if score >= 80:
            return "strong"
        elif score >= 60:
            return "good"
        elif score >= 40:
            return "moderate"
        return "limited"

    def _get_timestamp(self) -> str:
        """Get current timestamp in ISO format."""
        return datetime.utcnow().isoformat() + "Z"

    # ==================== Empty Result Helpers ====================

    def _empty_result(self, repo_name: str) -> Dict[str, Any]:
        """Return empty result structure."""
        return {
            "package_name": repo_name,
            "repository": "unknown",
            "timestamp": self._get_timestamp(),
            "maintenance_status": self._empty_maintenance(),
            "sustainability_indicators": self._empty_indicators(),
            "overall_score": {"score": 0, "max_score": 100, "status": "unknown"},
            "warnings": [],
        }

    def _empty_maintenance(self) -> Dict[str, Any]:
        return {
            "score": 50,
            "status": "unknown",
            "severity": "unknown",
            "is_archived": False,
            "is_disabled": False,
            "detected_warnings": {},
            "badge_warnings": [],
        }

    def _empty_indicators(self) -> Dict[str, Any]:
        return {
            "score": 50,
            "indicators": {},
            "active_signals": [],
            "positive_count": 0,
            "status": "unknown",
        }
