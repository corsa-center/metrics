"""
Spack dependency analyzer (CORSA metrics framework)

For each repository: the Spack package built from it, what that package
depends on and which Spack packages depend on it, with the version,
variant, dependency-type, platform and target conditions Spack records for
each edge. The orchestrator runs this
once per batch (see Orchestrator.collect_spack_dependencies) and stores
each package's entry in its metrics.json under `spack`;
tools/dashboard_data.py assembles the dashboard's
spackPackageDependencies.json and spackPackageDependents.json from those.

Ported from the dashboard's _explore/scripts/get_spack_dependencies.py.

A repository's package is the recipe whose homepage, url, git or any
version's url or git points at it (SCOREC/core -> pumi, SCOREC/omega_h ->
omega-h); when several do, the one named after the repository, else the one
most packages depend on. A repository no recipe points at falls back to a
recipe named after it, lower-cased, with "_" read as "-".

--package NAME=URL names the repository's package outright, for a
monorepo whose most-depended-on recipe isn't the one to show
(darshan-hpc/darshan builds darshan-util and darshan-runtime). It comes
from the catalog entry's `spack_package`; a name Spack has no recipe for is
ignored, and the repository is matched as above.

--repo adds a package repository after Spack's own, so it supplies only the
packages Spack's builtin repository lacks and cannot shadow a builtin
recipe of the same name.

Writes {repo_url: {"name", "dependencies", "dependents"}} to --output, with
null for a repository Spack has no package for.

Must run under Spack's own interpreter:
    spack python tools/spack_dependencies.py --output spack.json \\
        https://github.com/HDFGroup/hdf5 https://github.com/SCOREC/core
"""

import argparse
import json
import re
import sys
from collections import OrderedDict, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional

LANGUAGE_DEPENDENCIES = ["python", "cmake", "go", "rust", "lua", "fortran", "cxx", "c"]


def extract_arbitrary_attributes(attr_name, obj):
    ret = getattr(obj, attr_name, None)
    return str(ret) if ret is not None else "all"


def extract_languages(pkg_cls):
    deps = {name for constraints in pkg_cls.dependencies.values() for name in constraints}
    return [lang for lang in LANGUAGE_DEPENDENCIES if lang in deps]


def process_versions(pkg_versions):
    ret_versions = []
    for version in pkg_versions:
        str_ver = str(version)
        if str_ver == ":":
            ret_versions.append("all")
        elif ":" in str_ver:
            if str_ver[0] == ":":
                ret_versions.append(str_ver.replace(":", "<"))
            elif str_ver[-1] == ":":
                ret_versions.append(str_ver.replace(":", ">"))
            else:
                ret_versions.append(str_ver.replace(":", "<->"))
        else:
            ret_versions.append(str_ver)
    return ret_versions


def process_variants(variants):
    return [str(x) for x in variants.values()]


def process_build_flags(flags):
    names = {"b": "build", "r": "run", "l": "link", "t": "test"}
    return [name for flag, name in names.items() if flag in flags]


# scheme://[user@]host/path or git@host:path
_URL = re.compile(r"^(?:[a-z+]+://)?(?:[^@/]+@)?([^/:]+)[/:](.*)$", re.IGNORECASE)


def repo_location(url: str) -> Optional[str]:
    """host/path of a repository or download URL, lower-cased, without the
    scheme, a trailing slash or .git: comparable however it was written."""
    m = _URL.match((url or "").strip())
    if not m:
        return None
    path = m.group(2).strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return f"{m.group(1)}/{path}".lower() if path else None


def recipe_urls(pkg) -> List[str]:
    """Every URL a recipe fetches from or names as its home."""
    urls = []
    for attr in ("homepage", "url", "urls", "git", "list_url"):
        try:  # some are class properties computed from other attributes
            value = getattr(pkg, attr, None)
        except Exception:
            continue
        if isinstance(value, str):
            urls.append(value)
        elif isinstance(value, (list, tuple)):
            urls += [u for u in value if isinstance(u, str)]
    for args in (getattr(pkg, "versions", None) or {}).values():
        urls += [args[k] for k in ("url", "git") if isinstance(args.get(k), str)]
    return urls


def _names_for(repo_url: str) -> List[str]:
    name = (repo_location(repo_url) or "").rsplit("/", 1)[-1]
    return list(dict.fromkeys([name, name.replace("_", "-")])) if name else []


def match_recipes(repo_urls: Iterable[str], recipes: Dict[str, List[str]],
                  dependent_counts: Dict[str, int],
                  chosen: Optional[Dict[str, str]] = None) -> Dict[str, Optional[str]]:
    """{repo_url: Spack package name or None}, given each recipe's URLs
    (recipe_urls), how many packages depend on each recipe, and the
    packages catalog entries name outright ({repo_url: name})."""
    chosen = chosen or {}
    by_location = defaultdict(set)
    for name, urls in recipes.items():
        for url in urls:
            location = repo_location(url)
            if location:
                by_location[location].add(name)

    result = {}
    for repo_url in repo_urls:
        if chosen.get(repo_url) in recipes:
            result[repo_url] = chosen[repo_url]
            continue
        location = repo_location(repo_url)
        names = _names_for(repo_url)
        matched = set()
        if location:
            for loc, recipe_names in by_location.items():
                if loc == location or loc.startswith(location + "/"):
                    matched |= recipe_names
        if matched:
            named = [n for n in names if n in matched]
            result[repo_url] = named[0] if named else min(
                matched, key=lambda n: (-dependent_counts.get(n, 0), n))
        else:
            result[repo_url] = next((n for n in names if n in recipes), None)
    return result


def add_repositories(paths: List[str]) -> None:
    """Search these package repositories after the configured ones."""
    import spack.repo as sr

    if paths:
        sr.enable_repo(sr.RepoPath(*sr.PATH.repos, *(sr.from_path(p) for p in paths)))


def analyze(packages):
    """(dependencies, dependents) for the given Spack package names."""
    import spack.deptypes
    import spack.repo as sr

    dependency_db = defaultdict(OrderedDict)
    dependent_db = defaultdict(OrderedDict)
    for pkg in sr.PATH.all_package_classes():
        languages = extract_languages(pkg)
        for when, deps in pkg.dependencies.items():
            for name, dep in deps.items():
                spec = dep.spec
                dep_flags = process_build_flags(spack.deptypes.flag_to_chars(dep.depflag))
                if pkg.name in packages:
                    dependency_db[pkg.name][name] = {
                        "versions": process_versions(when.versions),
                        "variants": process_variants(when.variants),
                        "dep_flags": dep_flags,
                        "platform": extract_arbitrary_attributes("platform", when),
                        "target": extract_arbitrary_attributes("target", when),
                    }
                    dependency_db[pkg.name]["languages"] = languages
                if name in packages:
                    dependent_db[name][pkg.name] = {
                        "versions": process_versions(spec.versions),
                        "variants": process_variants(spec.variants),
                        "dep_flags": dep_flags,
                        "platform": extract_arbitrary_attributes("platform", spec),
                        "target": extract_arbitrary_attributes("target", spec),
                        "languages": languages,
                    }
    return dependency_db, dependent_db


def find_recipes(repo_urls: List[str], chosen: Optional[Dict[str, str]] = None) -> Dict[str, Optional[str]]:
    """{repo_url: Spack package name or None} over every recipe Spack has;
    `chosen` as in match_recipes."""
    import spack.repo as sr

    recipes, dependent_counts = {}, defaultdict(int)
    for pkg in sr.PATH.all_package_classes():
        recipes[pkg.name] = recipe_urls(pkg)
        for name in {name for deps in pkg.dependencies.values() for name in deps}:
            dependent_counts[name] += 1
    return match_recipes(repo_urls, recipes, dependent_counts, chosen)


def main(argv=None):
    parser = argparse.ArgumentParser("spack_dependencies", description=__doc__.split("\n\n")[1].strip())
    parser.add_argument("repo_urls", nargs="+", help="repository URLs to find Spack packages for")
    parser.add_argument("--output", type=Path, required=True, help="JSON file to write")
    parser.add_argument("--repo", action="append", default=[],
                        help="package repository (the directory holding repo.yaml) to search after Spack's own")
    parser.add_argument("--package", action="append", default=[], metavar="NAME=URL",
                        help="the Spack package to use for the repository at URL")
    args = parser.parse_args(argv)

    chosen = {}
    for value in args.package:
        name, _, url = value.partition("=")
        chosen[url] = name

    add_repositories(args.repo)
    names = find_recipes(args.repo_urls, chosen)
    found = {name for name in names.values() if name}
    dependencies, dependents = analyze(found)
    result = {
        repo_url: {
            "name": name,
            "dependencies": dependencies.get(name, {}),
            "dependents": dependents.get(name, {}),
        } if name else None
        for repo_url, name in names.items()
    }
    args.output.write_text(json.dumps(result, default=list))
    print(f"Found Spack packages for {len(found)} of {len(args.repo_urls)} repositories: "
          + ", ".join(f"{(repo_location(u) or u).split('/', 1)[-1]} -> {n}" for u, n in sorted(names.items()) if n))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
