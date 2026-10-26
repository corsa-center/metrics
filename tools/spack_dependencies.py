"""
Spack dependency analyzer (CASS metrics framework)

Writes the dashboard's spackPackageDependencies.json (what each tracked
package depends on) and spackPackageDependents.json (which Spack packages
depend on each tracked package), with the version, variant, dependency-type,
platform and target conditions Spack records for each edge.

Ported from the dashboard's _explore/scripts/get_spack_dependencies.py; the
package list now comes from the generated intReposInfo.json (see
tools/dashboard_data.py) instead of the dashboard's input_lists.json. A
tracked repository is matched to the Spack package named after its
repository name, lowercased; repositories with no such package are skipped.

Must run under Spack's own interpreter:
    spack python tools/spack_dependencies.py \\
        --repo-info data/20261026/intReposInfo.json --output data/20261026
"""

import argparse
import json
import sys
from collections import OrderedDict, defaultdict
from pathlib import Path

import spack
import spack.repo as sr

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


def analyze(packages):
    """(dependencies, dependents) for the given Spack package names."""
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


def packages_from_repo_info(repo_info_file):
    """Spack package names for the repositories in an intReposInfo.json."""
    with open(repo_info_file) as f:
        repos = json.load(f)["data"]
    names = {repo.split("/")[-1].lower() for repo in repos}
    return sorted(name for name in names if sr.PATH.exists(name))


def main(argv=None):
    parser = argparse.ArgumentParser("spack_dependencies", description=__doc__.split("\n\n")[1].strip())
    parser.add_argument("packages", nargs="*", help="Spack package names to analyze")
    parser.add_argument("--repo-info", type=Path, help="intReposInfo.json whose repositories to analyze")
    parser.add_argument("--output", type=Path, required=True, help="directory to write the two files into")
    args = parser.parse_args(argv)

    packages = list(args.packages)
    if args.repo_info:
        packages.extend(packages_from_repo_info(args.repo_info))
    if not packages:
        print("No Spack packages to analyze", file=sys.stderr)
        return 1

    dependencies, dependents = analyze(set(packages))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "spackPackageDependencies.json").write_text(json.dumps(dependencies, default=list))
    (args.output / "spackPackageDependents.json").write_text(json.dumps(dependents, default=list))
    print(f"Analyzed {len(packages)} Spack packages: {', '.join(sorted(packages))}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
