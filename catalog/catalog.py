"""
Catalog class to manage access to package catalog. This allows other tools to easily access the
catalog package information, as well as enabling different catalogs to be implemented in the future.
"""
import base64
import logging
from typing import Dict, Optional, List
from urllib.parse import urlparse

import httpx
import yaml

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# Catalog package schema
PACKAGE_CONFIG_SCHEMA_VERSION = 1


def _sanitize_package_config(data: Dict) -> Dict:
    """Coerce a package_config collectors:
    and overrides: blocks into well-formed dicts, dropping anything that
    isn't -- so every downstream reader can assume this without
    re-checking. This file is hand-edited YAML so can potentially
    leave a key present with no value (parses to None) or the wrong type
    entirely; treating that the same as
    the key being absent keeps this feature's "fails open" guarantee intact
    instead of raising deep in dict-chaining code that assumes it was
    already validated.
    """
    if not isinstance(data, dict):
        return {}
    result = dict(data)

    collectors = result.get("collectors")
    if isinstance(collectors, dict):
        result["collectors"] = {
            group: value
            for group, value in collectors.items()
            if isinstance(value, dict)
        }
    else:
        result.pop("collectors", None)

    overrides = result.get("overrides")
    if isinstance(overrides, dict):
        result["overrides"] = {
            section: labels
            for section, labels in overrides.items()
            if isinstance(labels, dict)
        }
    else:
        result.pop("overrides", None)

    repo_type = result.get("repo_type")
    if isinstance(repo_type, str) and repo_type.strip():
        result["repo_type"] = repo_type.strip().lower()
    else:
        result.pop("repo_type", None)

    return result


class Catalog:
    """Package catalog. If catalog requires credentials then these should be supplied when
    creating the class. Currently the catalog is a GitHub repository so credentials are assumed
    to be a GitHub token.
    """

    def __init__(self, catalog_url: Optional[str] = None, api_credentials: Optional[Dict] = None) -> None:
        """
        Initialize catalog
        :param catalog_url: URL of catalog
        :param api_credentials: API credentials for catalog
        """
        self.catalog_url = catalog_url
        self.api_credentials = api_credentials
        self.catalog = {}

    async def load(self) -> Dict:
        """Load the full software catalog from the catalog_url
        Returns: dictionary containing catalog metadata
        """
        if not self.catalog_url:
            raise RuntimeError(f"No catalog URL specified")
        catalog_files = self._fetch_catalog_files(self.catalog_url)
        if catalog_files is None:
            # Fail loudly: an empty catalog would let the run report success
            # having collected nothing.
            raise RuntimeError(f"Could not load the software catalog from {self.catalog_url}")
        catalog = {}
        for catalog_file in catalog_files:
            if catalog_file.get("type") != "file":
                continue
            file_name = catalog_file.get("name", "")
            if not file_name.endswith((".yaml", ".yml")):
                logger.warning(f"Skipping catalog file {file_name}: not a .yaml/.yml file")
                continue
            git_url = catalog_file["git_url"]
            package = await self._fetch_package_config(git_url)
            if not package:
                # One bad file shouldn't take every other package down with
                # it; _fetch_package_config has already logged why.
                logger.error(f"Skipping catalog file {file_name}: could not load a valid package")
                continue
            name = package["name"]
            if name in catalog:
                logger.error(
                    f"Skipping catalog file {file_name}: package name '{name}' "
                    f"is already used by another catalog file"
                )
                continue
            catalog[name] = package
        if not catalog:
            # Fail loudly: an empty catalog would let the run report success
            # having collected nothing.
            raise RuntimeError(f"No valid packages found in the catalog at {self.catalog_url}")
        return catalog

    def to_catalog(self, name: str, repo_url: str, repo_type: str) -> Dict:
        """
        Covert strings into a catalog entry

        :param name: name of the package
        :param repo_url:  url of package repository
        :param repo_type: type of repository
        :return: catalog dictionary
        """
        return {name: {"name": name, "repo_url": repo_url, "repo_type": repo_type}}

    def _fetch_catalog_files(self, url: str) -> Optional[List[Dict]]:
        """Fetch a list of configuration files for the catalog.
        Currently this is the contents of a directory from a GitHub URL.

        Args:
            url: URL to fetch

        Returns:
            Returns the contents in a consistent object format regardless of the content type
            or None if the url is not valid.
        """
        logger.info(f"Fetching {url}")

        try:
            headers = self._github_headers(url, "application/vnd.github.object+json")
            response = httpx.get(url, timeout=30.0, follow_redirects=True, headers=headers)
            response.raise_for_status()
            contents = response.json().get("entries")
            if contents is not None:
                return contents
            logger.error(f"{url} does not point to a GitHub directory")
        except Exception as e:
            logger.error(f"Failed to fetch {url}: {e}")
        return None

    async def _fetch_package_config(self, package_url) -> Dict:
        """Fetch a catalog package.
        """
        try:
            headers = self._github_headers(package_url, "application/vnd.github.v3+json")

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(package_url, headers=headers)
            if resp.status_code != 200:
                return {}
            content = base64.b64decode(resp.json().get("content", "")).decode(
                "utf-8", "replace"
            )
            package = yaml.safe_load(content) or {}
        except Exception as e:
            logger.warning(f"Could not fetch {package_url}: {e}")
            return {}

        if not isinstance(package, dict):
            logger.warning(f"Ignoring {package_url}: not a mapping")
            return {}
        if package.get("schema") != PACKAGE_CONFIG_SCHEMA_VERSION:
            logger.warning(
                f"Ignoring {package_url}: "
                f"unsupported schema {package.get('schema')!r}"
            )
            return {}
        missing = [k for k in ("name", "repo_type", "repo_url") if not package.get(k)]
        if missing:
            logger.warning(f"Ignoring {package_url}: missing required field(s) {missing}")
            return {}
        return _sanitize_package_config(package)

    def _github_headers(self, url: str, accept: str) -> Dict[str, str]:
        """Request headers for a GitHub API call. The token is attached only
        when `url` is on api.github.com, so a catalog_url pointed elsewhere
        can't leak it.
        """
        headers = {"Accept": accept}
        token = self._get_github_token()
        if token and urlparse(url).hostname == "api.github.com":
            headers["Authorization"] = f"token {token}"
        return headers

    def _get_github_token(self) -> Optional[str]:
        """Extract GitHub token from resolved config"""
        token = self.api_credentials.get("github", {}).get("token", "")
        return token if token else None

