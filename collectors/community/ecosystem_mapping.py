"""
Ecosystem Mapping Collector

Maps software packages across multiple platform ecosystems to assess
distribution reach, cross-platform availability, and ecosystem integration.

Package Managers Covered:
- General: PyPI, npm, RubyGems, crates.io, NuGet, Maven Central
- Scientific: Spack, conda-forge, Bioconda, CRAN, Bioconductor
- System: Homebrew, APT/Debian, Fedora/COPR, AUR
- Domain-specific: CPAN (Perl), Hackage (Haskell), MELPA (Emacs), vcpkg (C++)

Metrics Collected:
- ecosystem_coverage: Number and diversity of ecosystems where package exists
- version_consistency: How consistent versions are across ecosystems
- maintenance_activity: Recent updates across ecosystems
- installation_methods: Variety of installation options available
"""

import asyncio
import logging
import re
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass
import httpx

logger = logging.getLogger(__name__)


@dataclass
class EcosystemInfo:
    """Information about a package in an ecosystem."""
    name: str
    exists: bool
    version: Optional[str] = None
    last_updated: Optional[str] = None
    downloads: Optional[int] = None
    url: Optional[str] = None
    maintainers: Optional[List[str]] = None
    description: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class EcosystemMappingCollector:
    """
    Collects ecosystem mapping metrics by checking package availability
    across multiple package managers and distribution channels.
    """

    # Ecosystem categories for scoring
    ECOSYSTEM_CATEGORIES = {
        "general_purpose": ["pypi", "npm", "rubygems", "crates_io", "nuget", "maven"],
        "scientific": ["spack", "conda_forge", "bioconda", "cran", "bioconductor"],
        "system": ["homebrew", "apt_debian", "fedora", "aur", "nixpkgs"],
        "domain_specific": ["cpan", "hackage", "melpa", "vcpkg", "conan"],
    }

    # Weight multipliers for ecosystem importance (scientific software focus)
    ECOSYSTEM_WEIGHTS = {
        "spack": 1.5,
        "conda_forge": 1.5,
        "bioconda": 1.3,
        "pypi": 1.2,
        "cran": 1.3,
        "bioconductor": 1.3,
        "homebrew": 1.0,
        "apt_debian": 1.0,
        "nixpkgs": 1.0,
        "vcpkg": 1.0,
        "conan": 1.0,
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
        self.default_headers = {
            "Accept": "application/json",
            "User-Agent": "CASS-Metrics-Collector/1.0",
        }

    async def collect(self, package: Dict[str, Any]) -> Dict[str, Any]:
        """
        Main entry point - collects ecosystem mapping for a package.

        Args:
            package: Dictionary containing package info with 'name', 'repo_url',
                    and optionally 'aliases' for alternative package names

        Returns:
            Dictionary with all collected ecosystem metrics
        """
        package_name = package.get("name", "unknown")
        repo_url = package.get("repo_url", "")
        aliases = package.get("aliases", {})  # e.g., {"pypi": "hdf5", "conda": "hdf5"}

        logger.info(f"Collecting ecosystem mapping for {package_name}")

        try:
            # Check all ecosystems concurrently
            ecosystem_results = await asyncio.gather(
                # General purpose package managers
                self._check_pypi(package_name, aliases.get("pypi")),
                self._check_npm(package_name, aliases.get("npm")),
                self._check_crates_io(package_name, aliases.get("crates")),
                self._check_nuget(package_name, aliases.get("nuget")),
                self._check_rubygems(package_name, aliases.get("rubygems")),
                self._check_maven(package_name, aliases.get("maven")),
                # Scientific ecosystems
                self._check_spack(package_name, aliases.get("spack")),
                self._check_conda_forge(package_name, aliases.get("conda")),
                self._check_bioconda(package_name, aliases.get("bioconda")),
                self._check_cran(package_name, aliases.get("cran")),
                self._check_bioconductor(package_name, aliases.get("bioconductor")),
                # System package managers
                self._check_homebrew(package_name, aliases.get("homebrew")),
                self._check_apt_debian(package_name, aliases.get("apt")),
                self._check_fedora(package_name, aliases.get("fedora")),
                self._check_aur(package_name, aliases.get("aur")),
                self._check_nixpkgs(package_name, aliases.get("nix")),
                # Domain-specific
                self._check_vcpkg(package_name, aliases.get("vcpkg")),
                self._check_conan(package_name, aliases.get("conan")),
                self._check_cpan(package_name, aliases.get("cpan")),
                self._check_hackage(package_name, aliases.get("hackage")),
                return_exceptions=True,
            )

            # Map results to ecosystem names
            ecosystem_names = [
                "pypi", "npm", "crates_io", "nuget", "rubygems", "maven",
                "spack", "conda_forge", "bioconda", "cran", "bioconductor",
                "homebrew", "apt_debian", "fedora", "aur", "nixpkgs",
                "vcpkg", "conan", "cpan", "hackage",
            ]

            ecosystems: Dict[str, EcosystemInfo] = {}
            for name, result in zip(ecosystem_names, ecosystem_results):
                if isinstance(result, Exception):
                    logger.debug(f"Error checking {name}: {result}")
                    ecosystems[name] = EcosystemInfo(name=name, exists=False)
                else:
                    ecosystems[name] = result

            # Calculate metrics
            coverage = self._calculate_coverage(ecosystems)
            version_consistency = self._analyze_version_consistency(ecosystems)
            maintenance = self._analyze_maintenance_activity(ecosystems)
            installation_methods = self._categorize_installation_methods(ecosystems)

            # Calculate overall score
            overall_score = self._calculate_overall_score(
                coverage, version_consistency, maintenance, installation_methods
            )

            return {
                "package_name": package_name,
                "repository": self._extract_repo_name(repo_url),
                "timestamp": self._get_timestamp(),
                "ecosystem_coverage": coverage,
                "version_consistency": version_consistency,
                "maintenance_activity": maintenance,
                "installation_methods": installation_methods,
                "ecosystems": {
                    name: self._ecosystem_to_dict(info)
                    for name, info in ecosystems.items()
                },
                "overall_score": overall_score,
            }

        except Exception as e:
            logger.error(f"Error collecting ecosystem mapping for {package_name}: {e}")
            return self._empty_result(package_name)

    # ==================== PyPI ====================

    async def _check_pypi(
        self, package_name: str, alias: Optional[str] = None
    ) -> EcosystemInfo:
        """Check PyPI (Python Package Index)."""
        name = alias or package_name
        url = f"https://pypi.org/pypi/{name}/json"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, headers=self.default_headers)
                if response.status_code == 200:
                    data = response.json()
                    info = data.get("info", {})
                    releases = data.get("releases", {})

                    # Get latest release date
                    latest_version = info.get("version")
                    last_updated = None
                    if latest_version and latest_version in releases:
                        release_files = releases[latest_version]
                        if release_files:
                            last_updated = release_files[0].get("upload_time")

                    return EcosystemInfo(
                        name="pypi",
                        exists=True,
                        version=latest_version,
                        last_updated=last_updated,
                        url=f"https://pypi.org/project/{name}/",
                        maintainers=[info.get("author")] if info.get("author") else None,
                        description=info.get("summary"),
                        metadata={
                            "license": info.get("license"),
                            "requires_python": info.get("requires_python"),
                            "keywords": info.get("keywords"),
                        },
                    )
        except Exception as e:
            logger.debug(f"PyPI check failed for {name}: {e}")

        return EcosystemInfo(name="pypi", exists=False)

    # ==================== npm ====================

    async def _check_npm(
        self, package_name: str, alias: Optional[str] = None
    ) -> EcosystemInfo:
        """Check npm (Node.js Package Manager)."""
        name = alias or package_name
        url = f"https://registry.npmjs.org/{name}"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, headers=self.default_headers)
                if response.status_code == 200:
                    data = response.json()
                    dist_tags = data.get("dist-tags", {})
                    latest = dist_tags.get("latest")
                    time_info = data.get("time", {})

                    return EcosystemInfo(
                        name="npm",
                        exists=True,
                        version=latest,
                        last_updated=time_info.get("modified"),
                        url=f"https://www.npmjs.com/package/{name}",
                        maintainers=[m.get("name") for m in data.get("maintainers", [])],
                        description=data.get("description"),
                        metadata={
                            "license": data.get("license"),
                            "repository": data.get("repository"),
                        },
                    )
        except Exception as e:
            logger.debug(f"npm check failed for {name}: {e}")

        return EcosystemInfo(name="npm", exists=False)

    # ==================== crates.io ====================

    async def _check_crates_io(
        self, package_name: str, alias: Optional[str] = None
    ) -> EcosystemInfo:
        """Check crates.io (Rust Package Registry)."""
        name = alias or package_name
        url = f"https://crates.io/api/v1/crates/{name}"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, headers=self.default_headers)
                if response.status_code == 200:
                    data = response.json()
                    crate = data.get("crate", {})
                    versions = data.get("versions", [])

                    return EcosystemInfo(
                        name="crates_io",
                        exists=True,
                        version=crate.get("newest_version"),
                        last_updated=crate.get("updated_at"),
                        downloads=crate.get("downloads"),
                        url=f"https://crates.io/crates/{name}",
                        description=crate.get("description"),
                        metadata={
                            "categories": crate.get("categories"),
                            "keywords": crate.get("keywords"),
                        },
                    )
        except Exception as e:
            logger.debug(f"crates.io check failed for {name}: {e}")

        return EcosystemInfo(name="crates_io", exists=False)

    # ==================== NuGet ====================

    async def _check_nuget(
        self, package_name: str, alias: Optional[str] = None
    ) -> EcosystemInfo:
        """Check NuGet (.NET Package Manager)."""
        name = alias or package_name
        url = f"https://api.nuget.org/v3/registration5-semver1/{name.lower()}/index.json"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, headers=self.default_headers)
                if response.status_code == 200:
                    data = response.json()
                    items = data.get("items", [])
                    if items:
                        latest_page = items[-1]
                        catalog_entries = latest_page.get("items", [])
                        if catalog_entries:
                            latest = catalog_entries[-1].get("catalogEntry", {})
                            return EcosystemInfo(
                                name="nuget",
                                exists=True,
                                version=latest.get("version"),
                                last_updated=latest.get("published"),
                                url=f"https://www.nuget.org/packages/{name}",
                                description=latest.get("description"),
                                maintainers=latest.get("authors", "").split(",") if latest.get("authors") else None,
                            )
        except Exception as e:
            logger.debug(f"NuGet check failed for {name}: {e}")

        return EcosystemInfo(name="nuget", exists=False)

    # ==================== RubyGems ====================

    async def _check_rubygems(
        self, package_name: str, alias: Optional[str] = None
    ) -> EcosystemInfo:
        """Check RubyGems (Ruby Package Manager)."""
        name = alias or package_name
        url = f"https://rubygems.org/api/v1/gems/{name}.json"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, headers=self.default_headers)
                if response.status_code == 200:
                    data = response.json()
                    return EcosystemInfo(
                        name="rubygems",
                        exists=True,
                        version=data.get("version"),
                        downloads=data.get("downloads"),
                        url=data.get("project_uri"),
                        description=data.get("info"),
                        maintainers=data.get("authors", "").split(",") if data.get("authors") else None,
                        metadata={
                            "licenses": data.get("licenses"),
                        },
                    )
        except Exception as e:
            logger.debug(f"RubyGems check failed for {name}: {e}")

        return EcosystemInfo(name="rubygems", exists=False)

    # ==================== Maven Central ====================

    async def _check_maven(
        self, package_name: str, alias: Optional[str] = None
    ) -> EcosystemInfo:
        """Check Maven Central (Java Package Repository)."""
        name = alias or package_name
        # Maven search API
        url = f"https://search.maven.org/solrsearch/select?q=a:{name}&rows=1&wt=json"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, headers=self.default_headers)
                if response.status_code == 200:
                    data = response.json()
                    docs = data.get("response", {}).get("docs", [])
                    if docs:
                        doc = docs[0]
                        group_id = doc.get("g", "")
                        artifact_id = doc.get("a", "")
                        return EcosystemInfo(
                            name="maven",
                            exists=True,
                            version=doc.get("latestVersion"),
                            last_updated=datetime.fromtimestamp(
                                doc.get("timestamp", 0) / 1000
                            ).isoformat() if doc.get("timestamp") else None,
                            url=f"https://search.maven.org/artifact/{group_id}/{artifact_id}",
                            metadata={
                                "group_id": group_id,
                                "artifact_id": artifact_id,
                            },
                        )
        except Exception as e:
            logger.debug(f"Maven check failed for {name}: {e}")

        return EcosystemInfo(name="maven", exists=False)

    # ==================== Spack ====================

    async def _check_spack(
        self, package_name: str, alias: Optional[str] = None
    ) -> EcosystemInfo:
        """Check Spack (HPC Package Manager)."""
        name = alias or package_name
        # Check Spack packages via GitHub API (packages are in spack/spack repo)
        url = f"https://api.github.com/repos/spack/spack/contents/var/spack/repos/builtin/packages/{name.lower()}"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                headers = {**self.default_headers, **self.github_headers}
                response = await client.get(url, headers=headers)
                if response.status_code == 200:
                    # Package directory exists, get package.py for version info
                    pkg_url = f"https://raw.githubusercontent.com/spack/spack/develop/var/spack/repos/builtin/packages/{name.lower()}/package.py"
                    pkg_response = await client.get(pkg_url, headers=self.default_headers)

                    version = None
                    if pkg_response.status_code == 200:
                        content = pkg_response.text
                        # Extract latest version from package.py
                        version_matches = re.findall(
                            r'version\s*\(\s*["\']([^"\']+)["\']', content
                        )
                        if version_matches:
                            version = version_matches[0]

                    return EcosystemInfo(
                        name="spack",
                        exists=True,
                        version=version,
                        url=f"https://packages.spack.io/package.html?name={name.lower()}",
                        metadata={"source": "spack/spack"},
                    )
        except Exception as e:
            logger.debug(f"Spack check failed for {name}: {e}")

        return EcosystemInfo(name="spack", exists=False)

    # ==================== conda-forge ====================

    async def _check_conda_forge(
        self, package_name: str, alias: Optional[str] = None
    ) -> EcosystemInfo:
        """Check conda-forge (Community Conda Channel)."""
        name = alias or package_name
        url = f"https://api.anaconda.org/package/conda-forge/{name.lower()}"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, headers=self.default_headers)
                if response.status_code == 200:
                    data = response.json()
                    return EcosystemInfo(
                        name="conda_forge",
                        exists=True,
                        version=data.get("latest_version"),
                        downloads=data.get("conda_downloads"),
                        url=f"https://anaconda.org/conda-forge/{name.lower()}",
                        description=data.get("summary"),
                        metadata={
                            "platforms": data.get("conda_platforms"),
                            "license": data.get("license"),
                        },
                    )
        except Exception as e:
            logger.debug(f"conda-forge check failed for {name}: {e}")

        return EcosystemInfo(name="conda_forge", exists=False)

    # ==================== Bioconda ====================

    async def _check_bioconda(
        self, package_name: str, alias: Optional[str] = None
    ) -> EcosystemInfo:
        """Check Bioconda (Bioinformatics Conda Channel)."""
        name = alias or package_name
        url = f"https://api.anaconda.org/package/bioconda/{name.lower()}"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, headers=self.default_headers)
                if response.status_code == 200:
                    data = response.json()
                    return EcosystemInfo(
                        name="bioconda",
                        exists=True,
                        version=data.get("latest_version"),
                        downloads=data.get("conda_downloads"),
                        url=f"https://anaconda.org/bioconda/{name.lower()}",
                        description=data.get("summary"),
                        metadata={
                            "platforms": data.get("conda_platforms"),
                        },
                    )
        except Exception as e:
            logger.debug(f"Bioconda check failed for {name}: {e}")

        return EcosystemInfo(name="bioconda", exists=False)

    # ==================== CRAN ====================

    async def _check_cran(
        self, package_name: str, alias: Optional[str] = None
    ) -> EcosystemInfo:
        """Check CRAN (Comprehensive R Archive Network)."""
        name = alias or package_name
        url = f"https://crandb.r-pkg.org/{name}"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, headers=self.default_headers)
                if response.status_code == 200:
                    data = response.json()
                    return EcosystemInfo(
                        name="cran",
                        exists=True,
                        version=data.get("Version"),
                        last_updated=data.get("Date/Publication"),
                        url=f"https://cran.r-project.org/package={name}",
                        maintainers=[data.get("Maintainer")] if data.get("Maintainer") else None,
                        description=data.get("Title"),
                        metadata={
                            "license": data.get("License"),
                            "depends": data.get("Depends"),
                        },
                    )
        except Exception as e:
            logger.debug(f"CRAN check failed for {name}: {e}")

        return EcosystemInfo(name="cran", exists=False)

    # ==================== Bioconductor ====================

    async def _check_bioconductor(
        self, package_name: str, alias: Optional[str] = None
    ) -> EcosystemInfo:
        """Check Bioconductor (R Bioinformatics Packages)."""
        name = alias or package_name
        url = f"https://bioconductor.org/packages/json/3.18/bioc/{name}.json"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, headers=self.default_headers)
                if response.status_code == 200:
                    data = response.json()
                    return EcosystemInfo(
                        name="bioconductor",
                        exists=True,
                        version=data.get("Version"),
                        url=f"https://bioconductor.org/packages/{name}/",
                        maintainers=[data.get("Maintainer")] if data.get("Maintainer") else None,
                        description=data.get("Title"),
                        metadata={
                            "bioc_views": data.get("biocViews"),
                            "license": data.get("License"),
                        },
                    )
        except Exception as e:
            logger.debug(f"Bioconductor check failed for {name}: {e}")

        return EcosystemInfo(name="bioconductor", exists=False)

    # ==================== Homebrew ====================

    async def _check_homebrew(
        self, package_name: str, alias: Optional[str] = None
    ) -> EcosystemInfo:
        """Check Homebrew (macOS/Linux Package Manager)."""
        name = alias or package_name
        url = f"https://formulae.brew.sh/api/formula/{name.lower()}.json"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, headers=self.default_headers)
                if response.status_code == 200:
                    data = response.json()
                    versions = data.get("versions", {})
                    analytics = data.get("analytics", {}).get("install", {})

                    # Sum installs across time periods
                    total_installs = sum(
                        analytics.get(period, {}).get(name.lower(), 0)
                        for period in ["30d", "90d", "365d"]
                    )

                    return EcosystemInfo(
                        name="homebrew",
                        exists=True,
                        version=versions.get("stable"),
                        downloads=total_installs if total_installs else None,
                        url=f"https://formulae.brew.sh/formula/{name.lower()}",
                        description=data.get("desc"),
                        metadata={
                            "tap": data.get("tap"),
                            "license": data.get("license"),
                            "head_only": versions.get("head") and not versions.get("stable"),
                        },
                    )
        except Exception as e:
            logger.debug(f"Homebrew check failed for {name}: {e}")

        return EcosystemInfo(name="homebrew", exists=False)

    # ==================== APT/Debian ====================

    async def _check_apt_debian(
        self, package_name: str, alias: Optional[str] = None
    ) -> EcosystemInfo:
        """Check Debian/Ubuntu APT repositories."""
        name = alias or package_name
        # Use sources.debian.org API
        url = f"https://sources.debian.org/api/src/{name.lower()}/"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, headers=self.default_headers)
                if response.status_code == 200:
                    data = response.json()
                    versions = data.get("versions", [])
                    if versions:
                        latest = versions[0]
                        return EcosystemInfo(
                            name="apt_debian",
                            exists=True,
                            version=latest.get("version"),
                            url=f"https://packages.debian.org/search?keywords={name.lower()}",
                            metadata={
                                "suites": [v.get("suites", []) for v in versions[:3]],
                            },
                        )
        except Exception as e:
            logger.debug(f"Debian check failed for {name}: {e}")

        return EcosystemInfo(name="apt_debian", exists=False)

    # ==================== Fedora ====================

    async def _check_fedora(
        self, package_name: str, alias: Optional[str] = None
    ) -> EcosystemInfo:
        """Check Fedora packages."""
        name = alias or package_name
        url = f"https://src.fedoraproject.org/api/0/rpms/{name.lower()}"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, headers=self.default_headers)
                if response.status_code == 200:
                    data = response.json()
                    return EcosystemInfo(
                        name="fedora",
                        exists=True,
                        url=f"https://src.fedoraproject.org/rpms/{name.lower()}",
                        description=data.get("description"),
                        metadata={
                            "namespace": data.get("namespace"),
                        },
                    )
        except Exception as e:
            logger.debug(f"Fedora check failed for {name}: {e}")

        return EcosystemInfo(name="fedora", exists=False)

    # ==================== AUR ====================

    async def _check_aur(
        self, package_name: str, alias: Optional[str] = None
    ) -> EcosystemInfo:
        """Check Arch User Repository (AUR)."""
        name = alias or package_name
        url = f"https://aur.archlinux.org/rpc/v5/info/{name.lower()}"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, headers=self.default_headers)
                if response.status_code == 200:
                    data = response.json()
                    results = data.get("results", [])
                    if results:
                        pkg = results[0]
                        return EcosystemInfo(
                            name="aur",
                            exists=True,
                            version=pkg.get("Version"),
                            last_updated=datetime.fromtimestamp(
                                pkg.get("LastModified", 0)
                            ).isoformat() if pkg.get("LastModified") else None,
                            url=f"https://aur.archlinux.org/packages/{name.lower()}",
                            maintainers=[pkg.get("Maintainer")] if pkg.get("Maintainer") else None,
                            description=pkg.get("Description"),
                            metadata={
                                "votes": pkg.get("NumVotes"),
                                "popularity": pkg.get("Popularity"),
                            },
                        )
        except Exception as e:
            logger.debug(f"AUR check failed for {name}: {e}")

        return EcosystemInfo(name="aur", exists=False)

    # ==================== Nixpkgs ====================

    async def _check_nixpkgs(
        self, package_name: str, alias: Optional[str] = None
    ) -> EcosystemInfo:
        """Check Nixpkgs (Nix Package Manager)."""
        name = alias or package_name
        url = f"https://search.nixos.org/backend/latest-43-nixos-unstable/_search"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                # NixOS uses Elasticsearch
                query = {
                    "query": {
                        "bool": {
                            "must": [{"match": {"package_attr_name": name.lower()}}]
                        }
                    },
                    "size": 1,
                }
                headers = {**self.default_headers, "Content-Type": "application/json"}
                response = await client.post(url, json=query, headers=headers)
                if response.status_code == 200:
                    data = response.json()
                    hits = data.get("hits", {}).get("hits", [])
                    if hits:
                        source = hits[0].get("_source", {})
                        return EcosystemInfo(
                            name="nixpkgs",
                            exists=True,
                            version=source.get("package_version"),
                            url=f"https://search.nixos.org/packages?query={name.lower()}",
                            description=source.get("package_description"),
                            metadata={
                                "attr_name": source.get("package_attr_name"),
                                "platforms": source.get("package_platforms"),
                            },
                        )
        except Exception as e:
            logger.debug(f"Nixpkgs check failed for {name}: {e}")

        return EcosystemInfo(name="nixpkgs", exists=False)

    # ==================== vcpkg ====================

    async def _check_vcpkg(
        self, package_name: str, alias: Optional[str] = None
    ) -> EcosystemInfo:
        """Check vcpkg (Microsoft C++ Package Manager)."""
        name = alias or package_name
        # vcpkg packages are in microsoft/vcpkg repo
        url = f"https://api.github.com/repos/microsoft/vcpkg/contents/ports/{name.lower()}"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                headers = {**self.default_headers, **self.github_headers}
                response = await client.get(url, headers=headers)
                if response.status_code == 200:
                    # Get vcpkg.json for version info
                    vcpkg_url = f"https://raw.githubusercontent.com/microsoft/vcpkg/master/ports/{name.lower()}/vcpkg.json"
                    vcpkg_response = await client.get(vcpkg_url, headers=self.default_headers)

                    version = None
                    description = None
                    if vcpkg_response.status_code == 200:
                        try:
                            import json
                            vcpkg_data = json.loads(vcpkg_response.text)
                            version = vcpkg_data.get("version") or vcpkg_data.get("version-string")
                            description = vcpkg_data.get("description")
                            if isinstance(description, list):
                                description = " ".join(description)
                        except Exception:
                            pass

                    return EcosystemInfo(
                        name="vcpkg",
                        exists=True,
                        version=version,
                        url=f"https://vcpkg.io/en/package/{name.lower()}",
                        description=description,
                    )
        except Exception as e:
            logger.debug(f"vcpkg check failed for {name}: {e}")

        return EcosystemInfo(name="vcpkg", exists=False)

    # ==================== Conan ====================

    async def _check_conan(
        self, package_name: str, alias: Optional[str] = None
    ) -> EcosystemInfo:
        """Check Conan Center (C/C++ Package Manager)."""
        name = alias or package_name
        url = f"https://conan.io/center/api/ui/details?name={name.lower()}&user=_&channel=_"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, headers=self.default_headers)
                if response.status_code == 200:
                    data = response.json()
                    if data:
                        return EcosystemInfo(
                            name="conan",
                            exists=True,
                            version=data.get("latest_version"),
                            url=f"https://conan.io/center/recipes/{name.lower()}",
                            description=data.get("description"),
                            metadata={
                                "topics": data.get("topics"),
                                "licenses": data.get("licenses"),
                            },
                        )
        except Exception as e:
            logger.debug(f"Conan check failed for {name}: {e}")

        return EcosystemInfo(name="conan", exists=False)

    # ==================== CPAN ====================

    async def _check_cpan(
        self, package_name: str, alias: Optional[str] = None
    ) -> EcosystemInfo:
        """Check CPAN (Comprehensive Perl Archive Network)."""
        name = alias or package_name
        # CPAN uses :: as separator, try both
        cpan_name = name.replace("-", "::")
        url = f"https://fastapi.metacpan.org/v1/module/{cpan_name}"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, headers=self.default_headers)
                if response.status_code == 200:
                    data = response.json()
                    return EcosystemInfo(
                        name="cpan",
                        exists=True,
                        version=data.get("version"),
                        last_updated=data.get("date"),
                        url=f"https://metacpan.org/pod/{cpan_name}",
                        maintainers=[data.get("author")] if data.get("author") else None,
                        description=data.get("abstract"),
                    )
        except Exception as e:
            logger.debug(f"CPAN check failed for {name}: {e}")

        return EcosystemInfo(name="cpan", exists=False)

    # ==================== Hackage ====================

    async def _check_hackage(
        self, package_name: str, alias: Optional[str] = None
    ) -> EcosystemInfo:
        """Check Hackage (Haskell Package Archive)."""
        name = alias or package_name
        url = f"https://hackage.haskell.org/package/{name}.json"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, headers=self.default_headers)
                if response.status_code == 200:
                    data = response.json()
                    # Hackage returns version as key
                    versions = list(data.keys()) if isinstance(data, dict) else []
                    latest = versions[-1] if versions else None

                    return EcosystemInfo(
                        name="hackage",
                        exists=True,
                        version=latest,
                        url=f"https://hackage.haskell.org/package/{name}",
                    )
        except Exception as e:
            logger.debug(f"Hackage check failed for {name}: {e}")

        return EcosystemInfo(name="hackage", exists=False)

    # ==================== Analysis Methods ====================

    def _calculate_coverage(self, ecosystems: Dict[str, EcosystemInfo]) -> Dict[str, Any]:
        """Calculate ecosystem coverage metrics."""
        present = [name for name, info in ecosystems.items() if info.exists]
        total_checked = len(ecosystems)

        # Calculate by category
        category_coverage = {}
        for category, eco_list in self.ECOSYSTEM_CATEGORIES.items():
            category_present = [e for e in eco_list if e in present]
            category_coverage[category] = {
                "present": category_present,
                "count": len(category_present),
                "total": len(eco_list),
                "percentage": round(len(category_present) / len(eco_list) * 100, 1) if eco_list else 0,
            }

        # Weighted score
        weighted_sum = sum(
            self.ECOSYSTEM_WEIGHTS.get(eco, 1.0) for eco in present
        )
        max_weighted = sum(self.ECOSYSTEM_WEIGHTS.values())

        coverage_score = (weighted_sum / max_weighted) * 100 if max_weighted else 0

        return {
            "score": round(coverage_score, 2),
            "ecosystems_present": present,
            "count": len(present),
            "total_checked": total_checked,
            "raw_percentage": round(len(present) / total_checked * 100, 1),
            "by_category": category_coverage,
            "status": self._get_status(coverage_score),
        }

    def _analyze_version_consistency(
        self, ecosystems: Dict[str, EcosystemInfo]
    ) -> Dict[str, Any]:
        """Analyze version consistency across ecosystems."""
        versions = {}
        for name, info in ecosystems.items():
            if info.exists and info.version:
                # Normalize version string
                normalized = self._normalize_version(info.version)
                versions[name] = {
                    "raw": info.version,
                    "normalized": normalized,
                }

        if len(versions) < 2:
            return {
                "score": 100 if versions else 0,
                "versions": versions,
                "unique_versions": len(set(v["normalized"] for v in versions.values())),
                "consistent": True,
                "status": "excellent" if versions else "unknown",
            }

        # Check consistency
        normalized_versions = [v["normalized"] for v in versions.values()]
        unique = set(normalized_versions)
        consistency = 1 - (len(unique) - 1) / len(versions)

        # Find most common version
        from collections import Counter
        version_counts = Counter(normalized_versions)
        most_common = version_counts.most_common(1)[0] if version_counts else (None, 0)

        score = consistency * 100

        return {
            "score": round(score, 2),
            "versions": versions,
            "unique_versions": len(unique),
            "most_common_version": most_common[0],
            "ecosystems_on_latest": most_common[1],
            "consistent": len(unique) == 1,
            "outdated_ecosystems": [
                name for name, v in versions.items()
                if v["normalized"] != most_common[0]
            ],
            "status": self._get_status(score),
        }

    def _analyze_maintenance_activity(
        self, ecosystems: Dict[str, EcosystemInfo]
    ) -> Dict[str, Any]:
        """Analyze recent maintenance activity across ecosystems."""
        updates = {}
        now = datetime.utcnow()

        for name, info in ecosystems.items():
            if info.exists and info.last_updated:
                try:
                    # Parse various date formats
                    update_date = self._parse_date(info.last_updated)
                    if update_date:
                        days_ago = (now - update_date).days
                        updates[name] = {
                            "last_updated": info.last_updated,
                            "days_ago": days_ago,
                        }
                except Exception:
                    pass

        if not updates:
            return {
                "score": 0,
                "updates": {},
                "recently_updated": [],
                "stale": [],
                "status": "unknown",
            }

        # Categorize by freshness
        recently_updated = [n for n, u in updates.items() if u["days_ago"] <= 90]
        moderately_fresh = [n for n, u in updates.items() if 90 < u["days_ago"] <= 365]
        stale = [n for n, u in updates.items() if u["days_ago"] > 365]

        # Score based on recency
        total_ecosystems = len(updates)
        score = (
            (len(recently_updated) * 1.0 +
             len(moderately_fresh) * 0.5 +
             len(stale) * 0.1) / total_ecosystems * 100
        )

        return {
            "score": round(score, 2),
            "updates": updates,
            "recently_updated": recently_updated,
            "moderately_fresh": moderately_fresh,
            "stale": stale,
            "most_recent": min(updates.items(), key=lambda x: x[1]["days_ago"])[0] if updates else None,
            "status": self._get_status(score),
        }

    def _categorize_installation_methods(
        self, ecosystems: Dict[str, EcosystemInfo]
    ) -> Dict[str, Any]:
        """Categorize available installation methods."""
        present = {name: info for name, info in ecosystems.items() if info.exists}

        methods = {
            "language_specific": [],
            "system_packages": [],
            "scientific_hpc": [],
            "container_friendly": [],
        }

        language_managers = {"pypi", "npm", "crates_io", "nuget", "rubygems", "maven", "cpan", "hackage"}
        system_managers = {"homebrew", "apt_debian", "fedora", "aur", "nixpkgs"}
        scientific_managers = {"spack", "conda_forge", "bioconda", "cran", "bioconductor"}
        container_friendly = {"conda_forge", "bioconda", "pypi", "apt_debian", "nixpkgs"}

        for name in present:
            if name in language_managers:
                methods["language_specific"].append(name)
            if name in system_managers:
                methods["system_packages"].append(name)
            if name in scientific_managers:
                methods["scientific_hpc"].append(name)
            if name in container_friendly:
                methods["container_friendly"].append(name)

        # Calculate diversity score
        categories_covered = sum(1 for v in methods.values() if v)
        diversity_score = (categories_covered / len(methods)) * 100

        return {
            "score": round(diversity_score, 2),
            "methods": methods,
            "categories_covered": categories_covered,
            "total_categories": len(methods),
            "install_commands": self._generate_install_commands(present),
            "status": self._get_status(diversity_score),
        }

    def _generate_install_commands(
        self, present_ecosystems: Dict[str, EcosystemInfo]
    ) -> Dict[str, str]:
        """Generate install commands for present ecosystems."""
        commands = {}
        templates = {
            "pypi": "pip install {name}",
            "npm": "npm install {name}",
            "conda_forge": "conda install -c conda-forge {name}",
            "bioconda": "conda install -c bioconda {name}",
            "spack": "spack install {name}",
            "homebrew": "brew install {name}",
            "apt_debian": "apt install {name}",
            "crates_io": "cargo install {name}",
            "rubygems": "gem install {name}",
            "aur": "yay -S {name}",
            "nixpkgs": "nix-env -iA nixpkgs.{name}",
            "vcpkg": "vcpkg install {name}",
            "conan": "conan install {name}",
        }

        for eco_name, info in present_ecosystems.items():
            if eco_name in templates:
                # Use the actual package name from the URL if available
                pkg_name = info.url.split("/")[-1] if info.url else eco_name
                commands[eco_name] = templates[eco_name].format(name=pkg_name.lower())

        return commands

    def _calculate_overall_score(
        self,
        coverage: Dict[str, Any],
        version_consistency: Dict[str, Any],
        maintenance: Dict[str, Any],
        installation_methods: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Calculate overall ecosystem mapping score."""
        weights = {
            "coverage": 0.40,
            "version_consistency": 0.20,
            "maintenance": 0.20,
            "installation_methods": 0.20,
        }

        scores = {
            "coverage": coverage.get("score", 0),
            "version_consistency": version_consistency.get("score", 0),
            "maintenance": maintenance.get("score", 0),
            "installation_methods": installation_methods.get("score", 0),
        }

        weighted_sum = sum(scores[k] * weights[k] for k in weights)

        return {
            "score": round(weighted_sum, 2),
            "max_score": 100,
            "percentage": round(weighted_sum, 2),
            "status": self._get_status(weighted_sum),
            "component_scores": scores,
            "weights": weights,
        }

    # ==================== Utility Methods ====================

    def _normalize_version(self, version: str) -> str:
        """Normalize version string for comparison."""
        # Remove common prefixes and clean up
        version = re.sub(r"^[vV]", "", version)
        # Extract just major.minor.patch
        match = re.match(r"(\d+(?:\.\d+)*)", version)
        return match.group(1) if match else version

    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """Parse various date formats."""
        formats = [
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(date_str[:26], fmt)
            except ValueError:
                continue
        return None

    def _extract_repo_name(self, repo_url: str) -> str:
        """Extract owner/repo from GitHub URL."""
        match = re.search(r"github\.com/([^/]+/[^/]+?)(?:\.git)?/?$", repo_url)
        return match.group(1) if match else "unknown"

    def _get_status(self, score: float) -> str:
        """Get status label based on score."""
        if score >= 80:
            return "excellent"
        elif score >= 60:
            return "good"
        elif score >= 40:
            return "moderate"
        elif score >= 20:
            return "limited"
        else:
            return "minimal"

    def _get_timestamp(self) -> str:
        """Get current timestamp in ISO format."""
        return datetime.utcnow().isoformat() + "Z"

    def _ecosystem_to_dict(self, info: EcosystemInfo) -> Dict[str, Any]:
        """Convert EcosystemInfo to dictionary."""
        return {
            "exists": info.exists,
            "version": info.version,
            "last_updated": info.last_updated,
            "downloads": info.downloads,
            "url": info.url,
            "maintainers": info.maintainers,
            "description": info.description,
            "metadata": info.metadata,
        }

    def _empty_result(self, package_name: str) -> Dict[str, Any]:
        """Return empty result structure."""
        return {
            "package_name": package_name,
            "repository": "unknown",
            "timestamp": self._get_timestamp(),
            "ecosystem_coverage": {"score": 0, "count": 0, "status": "unknown"},
            "version_consistency": {"score": 0, "status": "unknown"},
            "maintenance_activity": {"score": 0, "status": "unknown"},
            "installation_methods": {"score": 0, "status": "unknown"},
            "ecosystems": {},
            "overall_score": {
                "score": 0,
                "max_score": 100,
                "percentage": 0,
                "status": "unknown",
            },
        }
