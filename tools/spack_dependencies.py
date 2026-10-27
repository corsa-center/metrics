"""
Spack dependency analyzer (CORSA metrics framework)

For each named Spack package: what it depends on and which Spack packages
depend on it, with the version, variant, dependency-type, platform and
target conditions Spack records for each edge. The orchestrator runs this
once per batch (see Orchestrator.collect_spack_dependencies) and stores
each package's entry in its metrics.json under `spack`;
tools/dashboard_data.py assembles the dashboard's
spackPackageDependencies.json and spackPackageDependents.json from those.

Ported from the dashboard's _explore/scripts/get_spack_dependencies.py.

Writes {name: {"name", "dependencies", "dependents"}} to --output, with
null for a name Spack has no package for.

Must run under Spack's own interpreter:
    spack python tools/spack_dependencies.py --output spack.json hdf5 adios2
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


def main(argv=None):
    parser = argparse.ArgumentParser("spack_dependencies", description=__doc__.split("\n\n")[1].strip())
    parser.add_argument("packages", nargs="+", help="Spack package names to analyze")
    parser.add_argument("--output", type=Path, required=True, help="JSON file to write")
    args = parser.parse_args(argv)

    found = {name for name in args.packages if sr.PATH.exists(name)}
    dependencies, dependents = analyze(found)
    result = {
        name: {
            "name": name,
            "dependencies": dependencies.get(name, {}),
            "dependents": dependents.get(name, {}),
        } if name in found else None
        for name in args.packages
    }
    args.output.write_text(json.dumps(result, default=list))
    print(f"Analyzed {len(found)} of {len(args.packages)} names as Spack packages: {', '.join(sorted(found))}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
