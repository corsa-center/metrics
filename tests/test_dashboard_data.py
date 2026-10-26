"""Tests for tools/dashboard_data.py (issue #93)."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.dashboard_data import build_dependencies, build_repos_info, load_packages, main

TODAY = datetime(2026, 10, 26, tzinfo=timezone.utc)


def _write(data_dir, day, repo, **metrics):
    path = data_dir / day / f"{repo}-metrics" / "metrics.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    metrics.setdefault("metadata", {"repo_url": f"https://github.com/Org/{repo}"})
    path.write_text(json.dumps(metrics))


def _repo_info(name_with_owner, **extra):
    return {"nameWithOwner": name_with_owner, "forks": {"totalCount": 1}, **extra}


class TestLoadPackages:
    def test_newest_day_wins(self, tmp_path):
        _write(tmp_path, "20261025", "hdf5", stars=1, repo_info=_repo_info("Org/hdf5", description="old"))
        _write(tmp_path, "20261026", "hdf5", stars=2, repo_info=_repo_info("Org/hdf5", description="new"))
        package = load_packages(tmp_path, today=TODAY)["hdf5"]
        assert package["stars"] == 2
        assert package["repo_info"]["description"] == "new"

    def test_a_gap_falls_back_to_an_older_day(self, tmp_path):
        # The collector leaves a key out when its lookup was a gap.
        _write(tmp_path, "20261024", "hdf5", dependencies={"languages": {"nodes": []}})
        _write(tmp_path, "20261025", "hdf5", repo_info=_repo_info("Org/hdf5"))
        _write(tmp_path, "20261026", "hdf5", stars=2)
        package = load_packages(tmp_path, today=TODAY)["hdf5"]
        assert package["stars"] == 2
        assert package["repo_info"]["nameWithOwner"] == "Org/hdf5"
        assert package["dependencies"] == {"languages": {"nodes": []}}

    def test_partial_days_are_combined(self, tmp_path):
        _write(tmp_path, "20261025", "hdf5", repo_info=_repo_info("Org/hdf5"))
        _write(tmp_path, "20261026", "adios2", repo_info=_repo_info("Org/adios2"))
        assert set(load_packages(tmp_path, today=TODAY)) == {"hdf5", "adios2"}

    def test_days_outside_the_window_are_ignored(self, tmp_path):
        _write(tmp_path, "20261001", "gone", repo_info=_repo_info("Org/gone"))
        _write(tmp_path, "20261026", "hdf5", repo_info=_repo_info("Org/hdf5"))
        assert set(load_packages(tmp_path, max_age_days=7, today=TODAY)) == {"hdf5"}

    def test_unpublished_package_is_left_out(self, tmp_path):
        _write(tmp_path, "20261025", "hdf5", repo_info=_repo_info("Org/hdf5"))
        _write(tmp_path, "20261026", "hdf5", metadata={"published": False})
        assert load_packages(tmp_path, today=TODAY) == {}

    def test_non_date_entries_and_bad_json_are_skipped(self, tmp_path):
        (tmp_path / "README.md").write_text("x")
        (tmp_path / "notes").mkdir()
        bad = tmp_path / "20261026" / "bad-metrics" / "metrics.json"
        bad.parent.mkdir(parents=True)
        bad.write_text("{")
        _write(tmp_path, "20261026", "hdf5", repo_info=_repo_info("Org/hdf5"))
        assert set(load_packages(tmp_path, today=TODAY)) == {"hdf5"}


class TestBuildRepos:
    def test_adds_stars_and_cdash(self):
        packages = {"hdf5": {
            "metadata": {"repo_url": "https://github.com/Org/hdf5", "cdash": "https://cdash/x"},
            "stars": 993, "repo_info": _repo_info("Org/hdf5"),
        }}
        entry = build_repos_info(packages)["data"]["Org/hdf5"]
        assert entry["stargazers"] == {"totalCount": 993}
        assert entry["cdash"] == "https://cdash/x"
        assert entry["forks"] == {"totalCount": 1}

    def test_package_without_repo_info_is_left_out(self):
        packages = {"hdf5": {"metadata": {}, "stars": 0}}
        assert build_repos_info(packages) == {"data": {}}

    def test_dependencies_keyed_by_name_with_owner(self):
        deps = {"languages": {"nodes": []}, "dependencyGraphManifests": {"nodes": []}}
        packages = {
            "hdf5": {"metadata": {"repo_url": "https://github.com/org/hdf5"},
                     "repo_info": _repo_info("HDFGroup/hdf5"), "dependencies": deps},
            # No repo_info: falls back to the catalog URL's path.
            "thing": {"metadata": {"repo_url": "https://gitlab.com/g/sub/thing.git"}, "dependencies": deps},
            "none": {"metadata": {"repo_url": "https://gitlab.com/g/none"}, "dependencies": None},
        }
        assert set(build_dependencies(packages)["data"]) == {"HDFGroup/hdf5", "g/sub/thing"}


class TestMain:
    def test_writes_both_files(self, tmp_path):
        data = tmp_path / "data"
        _write(data, datetime.now(timezone.utc).strftime("%Y%m%d"), "hdf5", stars=3,
               repo_info=_repo_info("Org/hdf5"), dependencies={"languages": {"nodes": []}})
        out = tmp_path / "out"
        assert main(["--data-dir", str(data), "--output", str(out)]) == 0
        assert json.loads((out / "intReposInfo.json").read_text())["data"]["Org/hdf5"]["stargazers"] == {"totalCount": 3}
        assert "Org/hdf5" in json.loads((out / "intRepos_Dependencies.json").read_text())["data"]

    def test_refuses_to_write_an_empty_catalog(self, tmp_path):
        data = tmp_path / "data"
        _write(data, datetime.now(timezone.utc).strftime("%Y%m%d"), "hdf5", stars=3)
        out = tmp_path / "out"
        assert main(["--data-dir", str(data), "--output", str(out)]) == 1
        assert not out.exists()
