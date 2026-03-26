"""
Licensing Metrics Collector

Collects metrics related to software licensing including:
- License file presence
- License type identification
- OSI approval status
- SPDX identifier
- License compatibility information
"""

import asyncio
import httpx
import logging
from typing import Dict, Any, Optional, List
import re

logger = logging.getLogger(__name__)


class LicensingCollector:
    """Collects licensing metrics from GitHub repositories"""

    # Common license file patterns
    LICENSE_PATTERNS = [
        "LICENSE",
        "LICENSE.md",
        "LICENSE.txt",
        "LICENCE",
        "LICENCE.md",
        "LICENCE.txt",
        "LICENSE-MIT",
        "LICENSE-APACHE",
        "COPYING",
        "COPYING.txt",
        "COPYRIGHT",
        "COPYRIGHT.txt",
        "license.md",
        "license.txt",
        "docs/LICENSE",
        "docs/LICENSE.md",
        ".github/LICENSE",
    ]

    # Common open source licenses with metadata
    KNOWN_LICENSES = {
        "MIT": {
            "name": "MIT License",
            "osi_approved": True,
            "spdx_id": "MIT",
            "category": "Permissive",
            "description": "Permissive license that allows commercial use",
        },
        "Apache-2.0": {
            "name": "Apache License 2.0",
            "osi_approved": True,
            "spdx_id": "Apache-2.0",
            "category": "Permissive",
            "description": "Permissive license with patent grant",
        },
        "GPL-2.0": {
            "name": "GNU General Public License v2.0",
            "osi_approved": True,
            "spdx_id": "GPL-2.0-only",
            "category": "Copyleft",
            "description": "Strong copyleft license",
        },
        "GPL-3.0": {
            "name": "GNU General Public License v3.0",
            "osi_approved": True,
            "spdx_id": "GPL-3.0-only",
            "category": "Copyleft",
            "description": "Strong copyleft license with patent provisions",
        },
        "LGPL-2.1": {
            "name": "GNU Lesser General Public License v2.1",
            "osi_approved": True,
            "spdx_id": "LGPL-2.1-only",
            "category": "Weak Copyleft",
            "description": "Weak copyleft license for libraries",
        },
        "LGPL-3.0": {
            "name": "GNU Lesser General Public License v3.0",
            "osi_approved": True,
            "spdx_id": "LGPL-3.0-only",
            "category": "Weak Copyleft",
            "description": "Weak copyleft license for libraries",
        },
        "BSD-2-Clause": {
            "name": 'BSD 2-Clause "Simplified" License',
            "osi_approved": True,
            "spdx_id": "BSD-2-Clause",
            "category": "Permissive",
            "description": "Permissive license similar to MIT",
        },
        "BSD-3-Clause": {
            "name": 'BSD 3-Clause "New" or "Revised" License',
            "osi_approved": True,
            "spdx_id": "BSD-3-Clause",
            "category": "Permissive",
            "description": "Permissive license with non-endorsement clause",
        },
        "MPL-2.0": {
            "name": "Mozilla Public License 2.0",
            "osi_approved": True,
            "spdx_id": "MPL-2.0",
            "category": "Weak Copyleft",
            "description": "File-level copyleft license",
        },
        "AGPL-3.0": {
            "name": "GNU Affero General Public License v3.0",
            "osi_approved": True,
            "spdx_id": "AGPL-3.0-only",
            "category": "Copyleft",
            "description": "Strong copyleft with network clause",
        },
        "Unlicense": {
            "name": "The Unlicense",
            "osi_approved": True,
            "spdx_id": "Unlicense",
            "category": "Public Domain",
            "description": "Public domain dedication",
        },
        "ISC": {
            "name": "ISC License",
            "osi_approved": True,
            "spdx_id": "ISC",
            "category": "Permissive",
            "description": "Permissive license similar to MIT",
        },
    }

    def __init__(self, github_token: Optional[str] = None):
        """Initialize collector with optional GitHub token"""
        self.github_token = github_token
        self.headers = {}
        if github_token:
            self.headers["Authorization"] = f"token {github_token}"
            self.headers["Accept"] = "application/vnd.github.v3+json"

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        """
        Collect licensing metrics for a package

        Args:
            package: Dictionary with 'name' and 'repo_url' keys

        Returns:
            Dictionary with licensing metrics
        """
        repo_name = package.get("name", "Unknown")
        repo_url = package.get("repo_url", "")

        logger.info(f"Collecting licensing metrics for {repo_name}")

        # Extract owner/repo from URL
        owner_repo = self._extract_owner_repo(repo_url)
        if not owner_repo:
            logger.error(f"Could not extract owner/repo from {repo_url}")
            return self._empty_result(repo_name)

        owner, repo = owner_repo

        # Try GitHub License API first (most accurate)
        license_api_result = await self._get_license_from_api(owner, repo)

        # Fallback: Check for license files manually
        if not license_api_result.get("found"):
            license_file_result = await self._check_license_file(owner, repo)
        else:
            license_file_result = license_api_result

        # Analyze license content
        license_analysis = self._analyze_license(license_file_result)

        return {
            "package_name": repo_name,
            "repository": f"{owner}/{repo}",
            "timestamp": self._get_timestamp(),
            "license_info": license_file_result,
            "license_analysis": license_analysis,
            "compliance_score": self._calculate_compliance_score(
                license_file_result, license_analysis
            ),
        }

    async def _get_license_from_api(self, owner: str, repo: str) -> Dict[str, Any]:
        """
        Get license information from GitHub License API
        This is the most accurate method as GitHub detects license type
        """
        logger.info(f"Checking GitHub License API for {owner}/{repo}")
        url = f"https://api.github.com/repos/{owner}/{repo}/license"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=self.headers)
                if response.status_code == 200:
                    data = response.json()

                    license_data = data.get("license", {})

                    return {
                        "found": True,
                        "source": "github_api",
                        "file_path": data.get("name", "LICENSE"),
                        "url": data.get("html_url", ""),
                        "size": data.get("size", 0),
                        "license_key": license_data.get("key", "unknown"),
                        "license_name": license_data.get("name", "Unknown"),
                        "spdx_id": license_data.get("spdx_id"),
                        "download_url": data.get("download_url", ""),
                        "content": None,  # Will fetch if needed
                    }
                else:
                    logger.debug(f"GitHub License API returned {response.status_code}")
                    return {"found": False, "source": "github_api"}
        except Exception as e:
            logger.debug(f"Error calling GitHub License API: {e}")
            return {"found": False, "source": "github_api"}

    async def _check_license_file(self, owner: str, repo: str) -> Dict[str, Any]:
        """Check for license file manually by trying common patterns"""
        logger.info(f"Manually checking for license files in {owner}/{repo}")

        for pattern in self.LICENSE_PATTERNS:
            result = await self._check_file_exists(owner, repo, pattern)
            if result["exists"]:
                # Try to fetch content
                content = ""
                if result.get("download_url"):
                    content = await self._get_file_content(result["download_url"])

                return {
                    "found": True,
                    "source": "manual_check",
                    "file_path": pattern,
                    "url": result["url"],
                    "size": result.get("size", 0),
                    "content": content,
                    "license_key": None,
                    "license_name": None,
                    "spdx_id": None,
                }

        return {"found": False, "source": "manual_check"}

    async def _check_file_exists(
        self, owner: str, repo: str, file_path: str
    ) -> Dict[str, Any]:
        """Check if a file exists in the repository"""
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{file_path}"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=self.headers)
                if response.status_code == 200:
                    data = response.json()
                    return {
                        "exists": True,
                        "url": data.get(
                            "html_url",
                            f"https://github.com/{owner}/{repo}/blob/main/{file_path}",
                        ),
                        "size": data.get("size", 0),
                        "download_url": data.get("download_url", ""),
                    }
                else:
                    return {"exists": False}
        except Exception as e:
            logger.debug(f"Error checking {file_path}: {e}")
            return {"exists": False}

    async def _get_file_content(self, download_url: str, max_size: int = 50000) -> str:
        """Get file content from download URL"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(download_url)
                if response.status_code == 200:
                    text = response.text
                    # Limit size to avoid huge files
                    return text[:max_size]
        except Exception as e:
            logger.debug(f"Error fetching file content: {e}")
        return ""

    def _analyze_license(self, license_info: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze license information and categorize"""
        if not license_info.get("found"):
            return {
                "license_type": None,
                "osi_approved": None,
                "category": None,
                "spdx_id": None,
                "description": "No license found",
            }

        # If we have SPDX ID from GitHub API, use it
        spdx_id = license_info.get("spdx_id")
        license_key = license_info.get("license_key")
        license_name = license_info.get("license_name")

        # Try to match against known licenses
        if spdx_id and spdx_id in self.KNOWN_LICENSES:
            license_meta = self.KNOWN_LICENSES[spdx_id]
            return {
                "license_type": license_name or license_meta["name"],
                "osi_approved": license_meta["osi_approved"],
                "category": license_meta["category"],
                "spdx_id": spdx_id,
                "description": license_meta["description"],
            }

        # Try to detect from content if available
        content = license_info.get("content", "")
        if content:
            detected = self._detect_license_from_content(content)
            if detected:
                return detected

        # Fallback: Use whatever GitHub gave us
        return {
            "license_type": license_name or "Unknown",
            "osi_approved": None,
            "category": "Unknown",
            "spdx_id": spdx_id,
            "description": f"License detected: {license_name}"
            if license_name
            else "Unknown license type",
        }

    def _detect_license_from_content(self, content: str) -> Optional[Dict[str, Any]]:
        """Try to detect license type from file content"""
        content_lower = content.lower()

        # Check for common license signatures
        if "mit license" in content_lower:
            return {
                "license_type": "MIT License",
                "osi_approved": True,
                "category": "Permissive",
                "spdx_id": "MIT",
                "description": "Detected from content: MIT License",
            }
        elif (
            "apache license, version 2.0" in content_lower
            or "apache-2.0" in content_lower
        ):
            return {
                "license_type": "Apache License 2.0",
                "osi_approved": True,
                "category": "Permissive",
                "spdx_id": "Apache-2.0",
                "description": "Detected from content: Apache 2.0",
            }
        elif "gnu general public license" in content_lower:
            if "version 3" in content_lower or "v3" in content_lower:
                return {
                    "license_type": "GPL-3.0",
                    "osi_approved": True,
                    "category": "Copyleft",
                    "spdx_id": "GPL-3.0-only",
                    "description": "Detected from content: GPL 3.0",
                }
            elif "version 2" in content_lower or "v2" in content_lower:
                return {
                    "license_type": "GPL-2.0",
                    "osi_approved": True,
                    "category": "Copyleft",
                    "spdx_id": "GPL-2.0-only",
                    "description": "Detected from content: GPL 2.0",
                }
        elif "bsd" in content_lower:
            if "3-clause" in content_lower or "modified" in content_lower:
                return {
                    "license_type": "BSD-3-Clause",
                    "osi_approved": True,
                    "category": "Permissive",
                    "spdx_id": "BSD-3-Clause",
                    "description": "Detected from content: BSD 3-Clause",
                }
            elif "2-clause" in content_lower or "simplified" in content_lower:
                return {
                    "license_type": "BSD-2-Clause",
                    "osi_approved": True,
                    "category": "Permissive",
                    "spdx_id": "BSD-2-Clause",
                    "description": "Detected from content: BSD 2-Clause",
                }

        return None

    def _calculate_compliance_score(
        self, license_info: Dict, analysis: Dict
    ) -> Dict[str, Any]:
        """Calculate licensing compliance score"""
        score = 0
        max_score = 3
        details = []

        # Check 1: License file exists
        if license_info.get("found"):
            score += 1
            details.append("License file exists: ✓")
        else:
            details.append("License file exists: ✗")

        # Check 2: License type identified
        if analysis.get("license_type") and analysis["license_type"] != "Unknown":
            score += 1
            details.append(f"License identified: ✓ ({analysis['license_type']})")
        else:
            details.append("License identified: ✗")

        # Check 3: OSI approved license
        if analysis.get("osi_approved"):
            score += 1
            details.append("OSI approved: ✓")
        elif analysis.get("osi_approved") is False:
            details.append("OSI approved: ✗")
        else:
            details.append("OSI approved: ? (unknown)")

        return {
            "score": score,
            "max_score": max_score,
            "percentage": round((score / max_score) * 100, 2),
            "details": details,
            "category": analysis.get("category", "Unknown"),
        }

    def _extract_owner_repo(self, repo_url: str) -> Optional[tuple]:
        """Extract owner and repo name from GitHub URL"""
        patterns = [
            r"github\.com/([^/]+)/([^/]+)",
            r"github\.com:([^/]+)/([^/]+)",
        ]

        for pattern in patterns:
            match = re.search(pattern, repo_url)
            if match:
                owner = match.group(1)
                repo = match.group(2).replace(".git", "")
                return (owner, repo)

        return None

    def _empty_result(self, repo_name: str) -> Dict[str, Any]:
        """Return empty result structure"""
        return {
            "package_name": repo_name,
            "repository": "unknown",
            "timestamp": self._get_timestamp(),
            "license_info": {"found": False},
            "license_analysis": {
                "license_type": None,
                "osi_approved": None,
                "category": None,
                "spdx_id": None,
                "description": "Could not analyze repository",
            },
            "compliance_score": {
                "score": 0,
                "max_score": 3,
                "percentage": 0,
                "details": [],
                "category": "Unknown",
            },
        }

    def _get_timestamp(self) -> str:
        """Get current timestamp"""
        from datetime import datetime

        return datetime.utcnow().isoformat() + "Z"
