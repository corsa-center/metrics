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

from forge.base import COLLECTION_GAP, RetryingTransport
from forge.interface import Forge

logger = logging.getLogger(__name__)

# What a forge's license detection reports when it can't name the license.
_UNNAMED_SPDX = {None, "", "NOASSERTION"}


class LicensingCollector:
    """Collects licensing metrics from GitHub repositories"""

    def __init__(self, forge: Forge):
        self.forge = forge

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

        ref = self.forge.extract_ref(repo_url)
        if not ref:
            logger.error(f"Could not extract a repo reference from {repo_url}")
            return self._empty_result(repo_name)

        async with httpx.AsyncClient(timeout=30.0, transport=RetryingTransport()) as client:
            # Try GitHub License API first (most accurate)
            license_api_result = await self._get_license_from_api(client, ref)

            # Fallback: Check for license files manually
            if not license_api_result.get("found"):
                license_file_result = await self._check_license_file(client, ref)
                # Neither step found a license -- only a real negative if
                # neither step gapped either; a gap on either one means the
                # license could be there and we just couldn't confirm it.
                if not license_file_result.get("found") and (
                    license_api_result.get("not_collected")
                    or license_file_result.get("not_collected")
                ):
                    license_file_result = {**license_file_result, "not_collected": True}
            else:
                license_file_result = license_api_result

        # Analyze license content
        license_analysis = self._analyze_license(license_file_result)

        return {
            "package_name": repo_name,
            "repository": ref,
            "timestamp": self.forge.get_timestamp(),
            "license_info": license_file_result,
            "license_analysis": license_analysis,
            "compliance_score": self._calculate_compliance_score(
                license_file_result, license_analysis
            ),
            # The orchestrator builds these 4.2.2 rows itself, so the
            # evidence the per-package report shows under them is keyed by
            # row label here.
            "row_evidence": self._row_evidence(
                ref, license_api_result, license_file_result, license_analysis
            ),
        }

    def _row_evidence(
        self, ref: str, api: Dict[str, Any], info: Dict[str, Any], analysis: Dict[str, Any]
    ) -> Dict[str, Dict]:
        """Where the license was found and what named it, or where it was
        looked for, for the Enhanced License Detection and OSI License
        Validation rows; neither row links the license file.

        A license the forge's detection can't name may still pass both rows
        through FairLicensingCollector's reading of the text; its License
        Exception Handling row says how, so these rows only say what this
        collector saw.
        """
        host = getattr(self.forge, "display_name", None) or "the forge"
        detection = f"{host}'s license detection"
        from_api = info.get("source") == "github_api"
        found, missing, searched, osi_missing, osi_searched = [], [], [], [], []

        if info.get("found"):
            # GitLab's detection is project-level and its file path only a
            # guess (see GitLabForge.license); a download URL means the
            # forge served the file it named.
            where = {}
            if not from_api or info.get("download_url"):
                where = {"path": info.get("file_path"), "url": info.get("url")}
            spdx = info.get("spdx_id")
            if from_api and spdx not in _UNNAMED_SPDX:
                found.append({"text": f"{spdx} license (identified by {detection})", **where})
            elif from_api:
                named = spdx or info.get("license_name") or "none"
                found.append({"text": f"{'License file' if where else 'A license'} "
                                      f"({detection} reported {named})", **where})
            elif analysis.get("matched"):
                phrases = ", ".join(f"“{p}”" for p in analysis["matched"])
                found.append({"text": f"{analysis['license_type']} (text matched {phrases})",
                              **where})
            else:
                found.append({"text": "License file", **where})
                if not api.get("not_collected"):
                    missing.append({"text": f"a license {detection} could name (it detected none)"})
                # The text is fetched best-effort; only text that was read was searched.
                if info.get("content"):
                    missing.append({"text": "a license this check recognizes in the text of "
                                            f"{info.get('file_path')} (MIT, Apache-2.0, GPL-2.0, "
                                            "GPL-3.0, BSD-2-Clause, BSD-3-Clause)"})

            spdx = analysis.get("spdx_id")
            unknown = analysis.get("osi_approved") is None
            if unknown and spdx not in _UNNAMED_SPDX:
                osi_missing.append({"text": f"{spdx} among the OSI-approved licenses "
                                            "this check recognizes"})
                osi_searched.append({"text": "OSI-approved licenses recognized",
                                     "paths": [{"path": k} for k in self.KNOWN_LICENSES]})
            elif unknown and (from_api or not api.get("not_collected")):
                reported = (spdx or info.get("license_name")) if from_api else None
                said = f"reported {reported}" if reported else "gave none"
                osi_missing.append({"text": "an SPDX license id to check against the "
                                            f"OSI-approved list ({detection} {said})"})
        elif not info.get("not_collected"):
            missing.append({"text": f"License file ({detection} found none)"})
            searched.append({"text": f"License file names tried in {ref}",
                             "paths": [{"path": p} for p in self.LICENSE_PATTERNS]})

        return {
            "license_detection": {
                "label": "Enhanced License Detection",
                "evidence": {"found": found, "missing": missing, "searched": searched},
            },
            "osi_validation": {
                "label": "OSI License Validation",
                "evidence": {"missing": osi_missing, "searched": osi_searched},
            },
        }

    async def _get_license_from_api(
        self, client: httpx.AsyncClient, ref: str
    ) -> Dict[str, Any]:
        """
        Get license information from GitHub License API
        This is the most accurate method as GitHub detects license type
        """
        logger.info(f"Checking GitHub License API for {ref}")
        data = await self.forge.license(client, ref)
        if data is COLLECTION_GAP:
            return {"found": False, "source": "github_api", "not_collected": True}
        if data is None:
            return {"found": False, "source": "github_api"}

        return {
            "found": True,
            "source": "github_api",
            "file_path": data["file_path"],
            "url": data["html_url"],
            "size": data["size"],
            "license_key": data["key"],
            "license_name": data["name"],
            "spdx_id": data["spdx_id"],
            "download_url": data["download_url"],
            "content": None,  # Will fetch if needed
        }

    async def _check_license_file(
        self, client: httpx.AsyncClient, ref: str
    ) -> Dict[str, Any]:
        """Check for license file manually by trying common patterns"""
        logger.info(f"Manually checking for license files in {ref}")

        saw_gap = False
        for pattern in self.LICENSE_PATTERNS:
            result = await self.forge.file_metadata(client, ref, pattern)
            if result is COLLECTION_GAP:
                saw_gap = True
                continue
            if result:
                content = ""
                if result.get("download_url"):
                    content = await self._get_file_content(client, result["download_url"])

                return {
                    "found": True,
                    "source": "manual_check",
                    "file_path": pattern,
                    "url": result["html_url"],
                    "size": result.get("size", 0),
                    "content": content,
                    "license_key": None,
                    "license_name": None,
                    "spdx_id": None,
                }

        result = {"found": False, "source": "manual_check"}
        if saw_gap:
            result["not_collected"] = True
        return result

    async def _get_file_content(
        self, client: httpx.AsyncClient, download_url: str, max_size: int = 50000
    ) -> str:
        """Get file content from download URL.

        Best-effort only: this only refines an already-found license's
        detected type, and _analyze_license already falls back to GitHub's
        own classification when content can't be read, so a failure here
        doesn't need its own not_collected tracking.
        """
        try:
            response = await client.get(download_url)
            if response.status_code == 200:
                return response.text[:max_size]
        except Exception as e:
            logger.debug(f"Error fetching file content: {e}")
        return ""

    def _analyze_license(self, license_info: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze license information and categorize"""
        if not license_info.get("found"):
            result = {
                "license_type": None,
                "osi_approved": None,
                "category": None,
                "spdx_id": None,
                "description": "No license found",
            }
            if license_info.get("not_collected"):
                result["description"] = "Could not determine license (collection gap)"
                result["not_collected"] = True
            return result

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

        # Try to detect from content if available — but only when GitHub's own
        # classifier didn't already look and actively fail to match a standard
        # license ("other"/NOASSERTION). Trust that over keyword matching:
        # a custom/derivative license can mention "BSD" or "3-Clause" in its
        # text without actually being BSD-3-Clause (e.g. HDF5's license), and
        # GitHub's classifier already ruled that out.
        github_could_not_classify = license_key == "other" or spdx_id == "NOASSERTION"
        content = license_info.get("content", "")
        if content and not github_could_not_classify:
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
        """Try to detect license type from file content. "matched" keeps the
        phrases that decided it, for the report."""
        content_lower = content.lower()

        def hit(*phrases: str) -> Optional[str]:
            return next((p for p in phrases if p in content_lower), None)

        # Check for common license signatures
        if m := hit("mit license"):
            return {
                "license_type": "MIT License",
                "osi_approved": True,
                "category": "Permissive",
                "spdx_id": "MIT",
                "description": "Detected from content: MIT License",
                "matched": [m],
            }
        elif m := hit("apache license, version 2.0", "apache-2.0"):
            return {
                "license_type": "Apache License 2.0",
                "osi_approved": True,
                "category": "Permissive",
                "spdx_id": "Apache-2.0",
                "description": "Detected from content: Apache 2.0",
                "matched": [m],
            }
        elif m := hit("gnu general public license"):
            if v := hit("version 3", "v3"):
                return {
                    "license_type": "GPL-3.0",
                    "osi_approved": True,
                    "category": "Copyleft",
                    "spdx_id": "GPL-3.0-only",
                    "description": "Detected from content: GPL 3.0",
                    "matched": [m, v],
                }
            elif v := hit("version 2", "v2"):
                return {
                    "license_type": "GPL-2.0",
                    "osi_approved": True,
                    "category": "Copyleft",
                    "spdx_id": "GPL-2.0-only",
                    "description": "Detected from content: GPL 2.0",
                    "matched": [m, v],
                }
        elif m := hit("bsd"):
            if v := hit("3-clause", "modified"):
                return {
                    "license_type": "BSD-3-Clause",
                    "osi_approved": True,
                    "category": "Permissive",
                    "spdx_id": "BSD-3-Clause",
                    "description": "Detected from content: BSD 3-Clause",
                    "matched": [m, v],
                }
            elif v := hit("2-clause", "simplified"):
                return {
                    "license_type": "BSD-2-Clause",
                    "osi_approved": True,
                    "category": "Permissive",
                    "spdx_id": "BSD-2-Clause",
                    "description": "Detected from content: BSD 2-Clause",
                    "matched": [m, v],
                }

        return None

    def _calculate_compliance_score(
        self, license_info: Dict, analysis: Dict
    ) -> Dict[str, Any]:
        """Calculate licensing compliance score"""
        score = 0
        max_score = 3
        details = []
        gap = bool(license_info.get("not_collected"))

        # Check 1: License file exists
        if license_info.get("found"):
            score += 1
            details.append("License file exists: ✓")
        elif gap:
            details.append("License file exists: ? (not collected)")
        else:
            details.append("License file exists: ✗")

        # Check 2: License type identified
        if analysis.get("license_type") and analysis["license_type"] != "Unknown":
            score += 1
            details.append(f"License identified: ✓ ({analysis['license_type']})")
        elif gap:
            details.append("License identified: ? (not collected)")
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

        # A gap that never confirmed the license one way or the other means
        # nothing here was actually measured -- report it as such instead of
        # a confident 0/3 or 1/3.
        if gap:
            return {
                "score": None, "max_score": 0, "percentage": None,
                "status": "not_collected", "details": details,
                "category": analysis.get("category", "Unknown"),
            }
        return {
            "score": score,
            "max_score": max_score,
            "percentage": round((score / max_score) * 100, 2),
            "details": details,
            "category": analysis.get("category", "Unknown"),
        }

    def _empty_result(self, repo_name: str) -> Dict[str, Any]:
        """Return empty result structure"""
        return {
            "package_name": repo_name,
            "repository": "unknown",
            "timestamp": self.forge.get_timestamp(),
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
