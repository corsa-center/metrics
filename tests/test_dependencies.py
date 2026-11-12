"""Tests for collectors/dependencies.py: the batch's Spack dependency data,
each package's dependencies, and the Spack match handed to the dimension
collectors."""

import asyncio
import re
import subprocess
from unittest.mock import patch

from collectors.dependencies import DependencyCollector, SpackDependencyCollector, with_spack_match
from tests.fakes import FakeForge


class TestDependencyInfo:
    PACKAGE = {"name": "P", "repo_type": "github", "repo_url": "https://github.com/o/HDF5"}

    def _collect(self, forge, spack_data=None):
        collector = DependencyCollector({})
        with patch.object(collector, "forge_for", return_value=forge):
            return asyncio.run(collector.collect(dict(self.PACKAGE), spack_data))

    def test_dependencies_collected(self):
        forge = FakeForge(dependency_data={"dependencyGraphManifests": {"nodes": []}})
        assert self._collect(forge) == {"dependencies": {"dependencyGraphManifests": {"nodes": []}}}

    def test_a_gap_leaves_the_key_out(self):
        assert self._collect(FakeForge(gaps={"dependency_graph"})) == {}

    def test_spack_entry_matched_by_repo_url(self):
        entry = {"name": "hdf5", "dependencies": {"zlib": {}}, "dependents": {}}
        assert self._collect(FakeForge(), {"https://github.com/o/HDF5": entry})["spack"] == entry

    def test_not_in_spack_is_recorded_as_none(self):
        assert self._collect(FakeForge(), {"https://github.com/o/HDF5": None})["spack"] is None

    def test_spack_unavailable_leaves_the_key_out(self):
        assert "spack" not in self._collect(FakeForge(), None)


class TestSpackMatchForCollectors:
    PACKAGE = {"name": "core", "repo_url": "https://github.com/SCOREC/core"}

    def test_match_is_passed_on_a_copy(self):
        spack_data = {self.PACKAGE["repo_url"]: {
            "name": "pumi", "dependencies": {"zlib": {}}, "dependents": {"a": {}, "b": {}}}}
        package = dict(self.PACKAGE)
        seen = with_spack_match(package, spack_data)
        assert seen["spack_match"] == {"name": "pumi", "dependent_packages": 2}
        assert "spack_match" not in package  # kept out of metrics.json's metadata

    def test_not_in_spack(self):
        assert with_spack_match(dict(self.PACKAGE), {self.PACKAGE["repo_url"]: None})["spack_match"] is None

    def test_spack_not_analyzed_leaves_it_out(self):
        assert "spack_match" not in with_spack_match(dict(self.PACKAGE), None)


class TestSpackDependencies:
    def _run(self, packages, spack_repos=None):
        return asyncio.run(SpackDependencyCollector({}).collect(packages, spack_repos))

    def test_no_spack_is_a_gap(self, monkeypatch):
        monkeypatch.setattr("collectors.dependencies.shutil.which", lambda _: None)
        assert self._run([{"repo_url": "https://github.com/o/r"}]) is None

    def _fake_spack(self, monkeypatch, tmp_path):
        # A stand-in `spack` whose `python` subcommand runs the helper's
        # contract: write {repo_url: entry} to --output.
        fake = tmp_path / "spack"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "args = sys.argv[3:]\n"
            "out = args[args.index('--output') + 1]\n"
            "urls = [a for a in args if a.startswith('https://')]\n"
            "open(out, 'w').write(json.dumps({u: None for u in urls}))\n"
            "open(sys.argv[0] + '.log', 'a').write(' '.join(args) + '\\n')\n"
        )
        fake.chmod(0o755)
        monkeypatch.setattr("collectors.dependencies.shutil.which", lambda _: str(fake))
        return tmp_path / "spack.log"

    def test_runs_the_helper_once_for_the_batch(self, monkeypatch, tmp_path):
        log = self._fake_spack(monkeypatch, tmp_path)
        packages = [{"repo_url": "https://gitlab.com/g/adios2.git"}, {"repo_url": "https://github.com/HDFGroup/HDF5"}]
        assert self._run(packages) == {
            "https://github.com/HDFGroup/HDF5": None, "https://gitlab.com/g/adios2.git": None}
        assert log.read_text().count("\n") == 1
        assert "--repo" not in log.read_text()

    def test_spack_repos_are_cloned_and_passed(self, monkeypatch, tmp_path):
        log = self._fake_spack(monkeypatch, tmp_path)
        source = tmp_path / "scorec"
        (source / "spack_repo" / "scorec").mkdir(parents=True)
        (source / "spack_repo" / "scorec" / "repo.yaml").write_text("repo:\n  namespace: scorec\n")
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "add", "."], check=True)
        subprocess.run(["git", "-C", str(source), "-c", "user.name=t", "-c", "user.email=t@t",
                        "commit", "-q", "-m", "x"], check=True)
        assert self._run([{"repo_url": "https://github.com/SCOREC/pcms"}], [f"file://{source}"]) == {
            "https://github.com/SCOREC/pcms": None}
        assert re.search(r"--repo \S+/repo0/spack_repo/scorec https://github.com/SCOREC/pcms", log.read_text())

    def test_a_failed_clone_is_a_gap(self, monkeypatch, tmp_path):
        log = self._fake_spack(monkeypatch, tmp_path)
        assert self._run([{"repo_url": "https://github.com/SCOREC/pcms"}],
                         [f"file://{tmp_path}/missing"]) is None
        assert not log.exists()

    def test_matches_and_misses_are_logged(self, caplog):
        import logging
        with caplog.at_level(logging.INFO, logger="collectors.dependencies"):
            SpackDependencyCollector._log_matches({
                "https://github.com/SCOREC/core": {"name": "pumi"},
                "https://github.com/SCOREC/pcms.git": None,
            }, {})
        assert "found for 1 of 2 repositories: SCOREC/core -> pumi" in caplog.text
        assert "Not in Spack: SCOREC/pcms" in caplog.text
        assert "spack_package" not in caplog.text

    def test_a_spack_package_spack_lacks_is_warned_about(self, caplog):
        import logging
        with caplog.at_level(logging.INFO, logger="collectors.dependencies"):
            SpackDependencyCollector._log_matches({
                "https://github.com/darshan-hpc/darshan": {"name": "darshan-runtime"},
                "https://github.com/SCOREC/core": {"name": "pumi"},
                "https://github.com/o/r": None,
            }, {
                "https://github.com/darshan-hpc/darshan": "darshan-runtime",
                "https://github.com/SCOREC/core": "pumi-typo",
                "https://github.com/o/r": "r-typo",
                "https://github.com/not/in-batch": "x",
            })
        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings == [
            "SCOREC/core: spack_package 'pumi-typo' is not a Spack package; matched pumi instead",
            "o/r: spack_package 'r-typo' is not a Spack package; matched none instead",
        ]

    def test_spack_package_is_passed_to_the_helper(self, monkeypatch, tmp_path):
        log = self._fake_spack(monkeypatch, tmp_path)
        packages = [
            {"repo_url": "https://github.com/darshan-hpc/darshan", "spack_package": " darshan-runtime "},
            {"repo_url": "https://github.com/HDFGroup/hdf5", "spack_package": None},
            {"repo_url": "https://github.com/o/r", "spack_package": ["not", "a", "name"]},
        ]
        self._run(packages)
        args = log.read_text().split()
        assert args.count("--package") == 1
        assert args[args.index("--package") + 1] == "darshan-runtime=https://github.com/darshan-hpc/darshan"

    def test_spack_repos_come_from_the_catalog(self):
        scorec = "https://github.com/SCOREC/scorec-spack-packages.git"
        catalog = [
            {"repo_url": "https://github.com/SCOREC/pcms", "spack_repo": scorec},
            {"repo_url": "https://github.com/SCOREC/pumi-pic", "spack_repo": [scorec, " https://x/y.git "]},
            {"repo_url": "https://github.com/HDFGroup/hdf5"},
            {"repo_url": "https://github.com/o/bad", "spack_repo": None},
            {"repo_url": "https://github.com/o/bad2", "spack_repo": {"git": "u"}},
        ]
        assert SpackDependencyCollector.spack_repos(catalog) == [scorec, "https://x/y.git"]

    def test_failure_is_a_gap(self, monkeypatch, tmp_path):
        fake = tmp_path / "spack"
        fake.write_text("#!/bin/sh\necho boom >&2\nexit 3\n")
        fake.chmod(0o755)
        monkeypatch.setattr("collectors.dependencies.shutil.which", lambda _: str(fake))
        assert self._run([{"repo_url": "https://github.com/o/r"}]) is None
