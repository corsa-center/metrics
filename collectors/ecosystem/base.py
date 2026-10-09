"""Platform-neutral helpers shared by ecosystem/quality collectors and
orchestrator.py: the threshold registry, the RepoTree path index, and the
file-pattern vocabularies several collectors agree on.

The HTTP plumbing that used to live here (COLLECTION_GAP, RetryingTransport,
GitHubCollectorBase) has moved to the forge/ package -- see forge/base.py
and forge/interface.py. COLLECTION_GAP and RetryingTransport are re-exported
here so existing imports keep working.
"""

import asyncio
import logging
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import httpx
import yaml

from forge.base import COLLECTION_GAP, RetryingTransport  # noqa: F401 (re-exported)
from forge.interface import Forge

logger = logging.getLogger(__name__)


# Directories holding someone else's code. A Dockerfile or spack.yaml inside
# a vendored dependency says nothing about the project itself.
_VENDORED_DIR = re.compile(
    r"(?:^|/)(?:external|extern|third[_-]?party|3rd[_-]?party|vendor|vendored"
    r"|node_modules|deps|tpls?|submodules)/",
    re.I,
)

# Container definitions, anywhere a project keeps them: SUNDIALS builds its
# images from scripts/docker/Dockerfile, which a root/docker/.docker
# candidate list never reached. Shared by reproducibility (4.3.3) and
# accessibility (4.3.5) so the two sections can't disagree.
# A suffix after the name is a variant (Dockerfile.cuda), unless it's a
# document about containers (Chimbuko: docs/installation/singularity.html).
_NOT_A_DEFINITION = r"(?![\w.-]*\.(?:html?|md|rst|txt|pdf|png|svg|py|sh)$)"
CONTAINER_FILE_PATTERNS = {
    "Docker": rf"(?:^|/)(?:Dockerfile|Containerfile){_NOT_A_DEFINITION}(?:\.[\w.-]+)?$"
              r"|\.(?:dockerfile|containerfile)$",
    "Singularity / Apptainer": rf"(?:^|/)(?:Singularity|Apptainer){_NOT_A_DEFINITION}(?:\.[\w.-]+)?$"
                               r"|(?:^|/)[\w.-]*(?:singularity|apptainer)[\w.-]*\.def$",
    "docker-compose": r"(?:^|/)(?:docker-)?compose\.ya?ml$",
}

# Declarative environment specifications (conda, Spack, devcontainer, and
# LLNL's Spack-driven uberenv), anywhere outside vendored code -- SUNDIALS
# keeps its Spack environments under scripts/docker/<config>/spack.yaml.
# Not under doc/ or docs/: that environment.yml builds the documentation
# (ADIOS2's Read the Docs config), not the software.
ENVIRONMENT_SPEC_PATTERN = (
    r"^(?!(?:.*/)?docs?/)(?:.*/)?(?:environment|conda[-_]env)[\w.-]*\.ya?ml$"
    r"|(?:^|/)spack\.(?:yaml|lock)$"
    r"|(?:^|/)\.devcontainer(?:/|\.json$)"
    r"|(?:^|/)\.uberenv_config\.json$"
    r"|(?:^|/)(?:shell|flake)\.nix$|(?:^|/)pixi\.toml$"
    # A Spack package recipe kept in the project's own repository.
    r"|(?:^|/)spack/(?:[^/]+/)*packages/[^/]+/package\.py$"
)


# Community channels a README can link to, shared by Multi-Channel
# Communication (4.2.3) and Decision-Making Visibility (4.2.6).
PUBLIC_CHANNEL_PATTERNS = {
    "Mailing list": re.compile(r"mailing[- ]list|listserv|groups\.google\.com|majordomo|\bmailman\b", re.I),
    "Chat (Slack/Discord/Matrix)": re.compile(r"slack\.com|discord\.(?:gg|com)|matrix\.to|gitter\.im|zulipchat", re.I),
    "Forum": re.compile(r"\bforum\b|discourse\.|stackoverflow\.com/questions/tagged", re.I),
    "Help desk": re.compile(r"help ?desk|support portal|jira|servicedesk", re.I),
}


# Fetched trees, shared by every collector in a run: about ten collectors
# each want the same package's tree, and on GitLab one tree is up to a few
# hundred paginated requests. Bounded LRU, since a large repository's index
# (llvm-project: ~150k paths) shouldn't be kept for the whole portfolio run;
# packages are collected a few at a time, so recent entries are the ones
# still being asked for. Gaps aren't cached -- a later collector retries.
_TREE_CACHE_SIZE = 8
_tree_cache: "OrderedDict[tuple, RepoTree]" = OrderedDict()
_tree_inflight: Dict[tuple, "asyncio.Future"] = {}


def _clear_tree_cache() -> None:
    """Test-only: module-level cache state must not leak between tests."""
    _tree_cache.clear()
    _tree_inflight.clear()


class RepoTree:
    """Case-insensitive index of every path in a repo's default-branch tree,
    fetched once and reused for every file/format check a collector needs.

    Fixes two related problems in one call:

    1. **Case sensitivity.** Per-path existence checks (`forge.file_exists`) are
       case-sensitive, so a literal "docs" never matches "Docs" and "tests"
       never matches "TESTING". `community_health.py` solved this for
       governance documents by listing directories and matching
       case-insensitively; this generalizes that fix to every other
       collector, instead of leaving each to reinvent it (or not).
    2. **Finite enumeration.** A candidate-path list can only match spellings
       someone thought to write down. `find()` searches the whole tree by
       regex, so "a getting-started guide, in any doc-shaped location"
       becomes one expression instead of six literal paths that AMReX's
       `Docs/sphinx_documentation/source/GettingStarted.rst` and 15 other
       portfolio repos still miss.

    One forge.repo_tree() call (a single recursive tree request on GitHub)
    replaces what could otherwise be a dozen-plus per-path existence probes
    per repository -- a net reduction in request volume, not just a
    correctness fix. URLs are built by the forge, so the same index works
    for GitHub and GitLab repositories.
    """

    def __init__(self, forge: Forge, ref: str, paths: List[str], truncated: bool):
        self.forge = forge
        self.ref = ref
        self.paths = paths
        self.truncated = truncated
        self._by_lower_path: Dict[str, str] = {p.lower(): p for p in paths}
        # Every directory a blob path implies, keyed case-insensitively with
        # its real casing as the value -- git doesn't track empty
        # directories, so a non-empty one is always inferable from its
        # files' paths without a second API call for tree entries.
        self._by_lower_dir: Dict[str, str] = {}
        for p in paths:
            parts = p.split("/")
            for i in range(1, len(parts)):
                d = "/".join(parts[:i])
                self._by_lower_dir.setdefault(d.lower(), d)

    @classmethod
    async def fetch(cls, client: httpx.AsyncClient, forge: Forge, ref: str):
        """A RepoTree for the default branch, or COLLECTION_GAP if the tree
        couldn't be fetched (including a confirmed-missing repository, which
        _confirm_repo_exists screens out before collection anyway).

        Cached per (platform, host, ref) and de-duplicated while in flight;
        see _tree_cache."""
        key = (forge.platform, forge.host, ref)
        if key in _tree_cache:
            _tree_cache.move_to_end(key)
            return _tree_cache[key]
        loop_key = (id(asyncio.get_running_loop()),) + key
        pending = _tree_inflight.get(loop_key)
        if pending is not None:
            return await asyncio.shield(pending)
        future = asyncio.get_running_loop().create_future()
        _tree_inflight[loop_key] = future
        try:
            tree = await cls._fetch_uncached(client, forge, ref)
            if tree is not COLLECTION_GAP:
                _tree_cache[key] = tree
                while len(_tree_cache) > _TREE_CACHE_SIZE:
                    _tree_cache.popitem(last=False)
            future.set_result(tree)
            return tree
        except BaseException as e:
            future.set_exception(e)
            future.exception()  # mark retrieved when nobody else is waiting
            raise
        finally:
            _tree_inflight.pop(loop_key, None)

    @classmethod
    async def _fetch_uncached(cls, client: httpx.AsyncClient, forge: Forge, ref: str):
        data = await forge.repo_tree(client, ref)
        if not data or data is COLLECTION_GAP:
            return COLLECTION_GAP
        paths = [f["path"] for f in data.get("files", [])]
        return cls(forge, ref, paths, bool(data.get("truncated")))

    def match(self, candidates: List[str]) -> Optional[str]:
        """First candidate present in the tree as a file OR a directory,
        matched case-insensitively and in candidate order. Returns the path
        with its real casing (not the candidate's), or None if none of them
        are there.

        Checking both is what per-path existence checks do too (the Contents API
        returns either a file or a directory listing for the same path) --
        a candidate like "test/googletest" is a vendored subdirectory, not a
        file, and still needs to match.
        """
        for candidate in candidates:
            key = candidate.lower().rstrip("/")
            hit = self._by_lower_path.get(key) or self._by_lower_dir.get(key)
            if hit is not None:
                return hit
        return None

    def match_url(self, candidates: List[str]) -> Optional[str]:
        """Same as match(), rendered as a browsable URL on the forge."""
        path = self.match(candidates)
        if path is None:
            return None
        kind = "blob" if path.lower() in self._by_lower_path else "tree"
        return self.forge.web_url(self.ref, path, kind)

    def has_dir(self, path: str) -> bool:
        """Whether this exact directory path exists, case-insensitively --
        "docs" matches a real "Docs/" tree. `path` can be nested
        (".github/ISSUE_TEMPLATE"), but is matched as a full path from the
        repo root, not as a basename search at arbitrary depth.
        """
        return path.lower().rstrip("/") in self._by_lower_dir

    def find(self, pattern: str, flags: int = re.IGNORECASE) -> List[str]:
        """Full paths anywhere in the tree matching a regex, in tree order.
        For "does the concept exist, under any name" checks that a fixed
        candidate list can't express.
        """
        rx = re.compile(pattern, flags)
        return [p for p in self.paths if rx.search(p)]

    def find_owned(self, pattern: str, flags: int = re.IGNORECASE) -> Optional[str]:
        """Shallowest path matching a regex outside vendored directories,
        or None. For "does the project itself ship one of these, wherever
        it keeps it" checks."""
        hits = [p for p in self.find(pattern, flags) if not _VENDORED_DIR.search(p)]
        return min(hits, key=lambda p: (p.count("/"), p)) if hits else None

    def url_for(self, path: str) -> str:
        return self.forge.web_url(self.ref, path)

    def find_url(self, pattern: str, flags: int = re.IGNORECASE) -> Optional[str]:
        """First find() hit, rendered as a browsable URL on the forge."""
        hits = self.find(pattern, flags)
        if not hits:
            return None
        return self.forge.web_url(self.ref, hits[0])


# --------------------------------------------------------------------------- #
# Threshold registry                                                          #
# --------------------------------------------------------------------------- #
# See config/thresholds.yaml for the values themselves and the full design
# rationale. In short: every configurable pass/fail threshold used to be a
# magic number duplicated (or, in three cases, defined but never actually
# read) across collectors and orchestrator.py's rendering code. This module
# is now the one place that resolves a threshold value, whether the call
# site is a collector deciding its own sub_scores or orchestrator.py
# deciding what to render.

_ThresholdValue = Union[int, float, List[str]]

_THRESHOLDS_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "thresholds.yaml"


class ThresholdRegistry:
    """Resolves a (section, label[, param]) key to its configured value.

    Defaults are loaded once from config/thresholds.yaml. Overrides (from
    config/orchestrator.yaml's own `thresholds:` block today; per-project and
    per-package overrides are not wired up yet) are applied on top via
    `set_overrides`, called once at orchestrator startup -- well before any
    collector's `collect()` runs, so every `get()` call during an actual run
    sees the final, merged value.
    """

    def __init__(self, defaults_path: Path):
        self._defaults_path = defaults_path
        self._defaults: Dict[str, Dict[str, Any]] = self._load(defaults_path)
        self._overrides: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def _load(path: Path) -> Dict[str, Dict[str, Any]]:
        if not path.exists():
            return {}
        data = yaml.safe_load(path.read_text()) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{path} must be a mapping of section -> label -> value")
        return data

    def set_overrides(self, overrides: Optional[Dict[str, Dict[str, Any]]]) -> None:
        """Install config-file overrides, validating each key exists first.

        An override for a (section, label) or (section, label, param) that
        isn't already in the defaults is almost always a typo -- the whole
        point of this registry is that a threshold with nobody reading it
        fails immediately, not silently, so this raises rather than storing
        an override nothing will ever look up.
        """
        overrides = overrides or {}
        for section, labels in overrides.items():
            if not isinstance(labels, dict):
                raise ValueError(
                    f"thresholds override for section {section!r} must be a mapping "
                    f"of sub-metric label -> value"
                )
            default_labels = self._defaults.get(section)
            if default_labels is None:
                raise ValueError(
                    f"thresholds override references section {section!r}, which has "
                    f"no defaults in {self._defaults_path} -- not a recognized "
                    f"threshold section"
                )
            for label, value in labels.items():
                if label not in default_labels:
                    raise ValueError(
                        f"thresholds override references {section!r} / {label!r}, "
                        f"which has no default -- not a recognized threshold"
                    )
                default_value = default_labels[label]
                if value is None:
                    raise ValueError(
                        f"thresholds override for {section!r} / {label!r} is null "
                        f"-- omit the key entirely instead of overriding to no value"
                    )
                if isinstance(default_value, dict):
                    if not isinstance(value, dict):
                        raise ValueError(
                            f"thresholds override for {section!r} / {label!r} must "
                            f"be a mapping of parameter name -> value, since the "
                            f"default has named parameters -- got "
                            f"{type(value).__name__}"
                        )
                    unknown = set(value) - set(default_value)
                    if unknown:
                        raise ValueError(
                            f"thresholds override for {section!r} / {label!r} sets "
                            f"unrecognized parameter(s) {sorted(unknown)}"
                        )
                elif isinstance(value, dict):
                    raise ValueError(
                        f"thresholds override for {section!r} / {label!r} is a "
                        f"mapping, but the default is a single value"
                    )
        self._overrides = overrides

    def get(self, section: str, label: str, param: Optional[str] = None) -> _ThresholdValue:
        """Resolve one threshold value, overrides applied.

        Raises KeyError if (section, label[, param]) has no default at
        all -- that's a call site bug (referencing a threshold this
        registry was never told about), not a config problem, so it's not
        caught by set_overrides's validation and fails at the call site
        instead.
        """
        try:
            default_value = self._defaults[section][label]
        except KeyError:
            raise KeyError(
                f"no threshold default for {section!r} / {label!r} -- add one to "
                f"{self._defaults_path} first"
            ) from None

        override_value = self._overrides.get(section, {}).get(label)

        if isinstance(default_value, dict):
            if param is None:
                raise KeyError(
                    f"threshold {section!r} / {label!r} has named parameters -- "
                    f"call with param=<name>, not a bare lookup"
                )
            if param not in default_value:
                raise KeyError(
                    f"no threshold default for {section!r} / {label!r} / {param!r}"
                )
            if isinstance(override_value, dict) and param in override_value:
                return override_value[param]
            return default_value[param]

        if param is not None:
            raise KeyError(
                f"threshold {section!r} / {label!r} has no parameters "
                f"(it's a single value) -- called with param={param!r}"
            )
        if override_value is not None:
            return override_value
        return default_value


    def section(self, section: str) -> Dict[str, Any]:
        """Every threshold configured for one section, overrides applied.

        Used by the per-project report to state which values a run was
        actually judged against; {} for a section with nothing configurable.
        """
        resolved: Dict[str, Any] = {}
        for label, default_value in self._defaults.get(section, {}).items():
            if isinstance(default_value, dict):
                resolved[label] = {
                    param: self.get(section, label, param) for param in default_value
                }
            else:
                resolved[label] = self.get(section, label)
        return resolved


_REGISTRY = ThresholdRegistry(_THRESHOLDS_PATH)


def get_threshold(section: str, label: str, param: Optional[str] = None) -> _ThresholdValue:
    """Module-level convenience wrapper around the shared registry instance."""
    return _REGISTRY.get(section, label, param)


def get_section_thresholds(section: str) -> Dict[str, Any]:
    """Module-level convenience wrapper around ThresholdRegistry.section."""
    return _REGISTRY.section(section)


def configure_threshold_overrides(overrides: Optional[Dict[str, Dict[str, Any]]]) -> None:
    """Install threshold overrides from config/orchestrator.yaml's `thresholds:`
    block. Call once at startup, before any collector runs."""
    _REGISTRY.set_overrides(overrides)
