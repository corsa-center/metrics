"""Tests for tools/dashboard_data.py (issue #93)."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.dashboard_data import build_dependencies, build_repos_info, build_spack, load_packages, main

TODAY = datetime(2026, 10, 26, tzinfo=timezone.utc)


def _write(data_dir, day, repo, metadata=None, **metrics):
    path = data_dir / day / f"{repo}-metrics" / "metrics.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    metrics["metadata"] = {"repo_url": f"https://github.com/Org/{repo}", **(metadata or {})}
    path.write_text(json.dumps(metrics))


def _package(repo_url="https://github.com/Org/hdf5", **metadata):
    return {"metadata": {"repo_url": repo_url, **metadata}}


class TestLoadPackages:
    def test_newest_day_wins(self, tmp_path):
        _write(tmp_path, "20261025", "hdf5", {"description": "old", "stars": 1})
        _write(tmp_path, "20261026", "hdf5", {"description": "new", "stars": 2})
        package = load_packages(tmp_path, today=TODAY)["hdf5"]
        assert package["metadata"]["stars"] == 2
        assert package["metadata"]["description"] == "new"

    def test_a_gap_falls_back_to_an_older_day(self, tmp_path):
        # The collector leaves a key out when its lookup was a gap.
        _write(tmp_path, "20261024", "hdf5", dependencies={"languages": {"nodes": []}})
        _write(tmp_path, "20261025", "hdf5", {"issues": {"open": 1, "closed": 2}, "stars": 1}, spack=None)
        _write(tmp_path, "20261026", "hdf5", {"description": "new"})
        package = load_packages(tmp_path, today=TODAY)["hdf5"]
        assert package["metadata"]["stars"] == 1
        assert package["metadata"]["description"] == "new"
        assert package["metadata"]["issues"] == {"open": 1, "closed": 2}
        assert package["dependencies"] == {"languages": {"nodes": []}}
        assert package["spack"] is None

    def test_a_repository_lookup_gap_takes_the_last_good_day(self, tmp_path):
        good = {"created_at": "2018-11-06T15:18:42Z", "language": "Python", "description": "from repo",
                "license": {"spdx_id": "BSD-3-Clause", "name": "BSD", "key": "bsd-3-clause"}}
        _write(tmp_path, "20261021", "deephyper", {**good, "description": "older"})
        _write(tmp_path, "20261022", "deephyper", good)
        _write(tmp_path, "20261026", "deephyper", {"description": "from catalog", "published": False})
        metadata = load_packages(tmp_path, today=TODAY)["deephyper"]["metadata"]
        assert metadata["created_at"] == "2018-11-06T15:18:42Z"
        assert metadata["language"] == "Python"
        assert metadata["description"] == "from repo"
        assert metadata["license"]["key"] == "bsd-3-clause"
        assert metadata["published"] is False

    def test_partial_days_are_combined(self, tmp_path):
        _write(tmp_path, "20261025", "hdf5")
        _write(tmp_path, "20261026", "adios2")
        assert set(load_packages(tmp_path, today=TODAY)) == {"hdf5", "adios2"}

    def test_days_outside_the_window_are_ignored(self, tmp_path):
        _write(tmp_path, "20261001", "gone")
        _write(tmp_path, "20261026", "hdf5")
        assert set(load_packages(tmp_path, max_age_days=7, today=TODAY)) == {"hdf5"}

    def test_unpublished_package_is_kept(self, tmp_path):
        _write(tmp_path, "20261026", "hdf5", {"published": False})
        assert load_packages(tmp_path, today=TODAY)["hdf5"]["metadata"]["published"] is False

    def test_non_date_entries_and_bad_json_are_skipped(self, tmp_path):
        (tmp_path / "README.md").write_text("x")
        (tmp_path / "notes").mkdir()
        bad = tmp_path / "20261026" / "bad-metrics" / "metrics.json"
        bad.parent.mkdir(parents=True)
        bad.write_text("{")
        _write(tmp_path, "20261026", "hdf5")
        assert set(load_packages(tmp_path, today=TODAY)) == {"hdf5"}


class TestBuildRepos:
    def test_github_graphql_shape_from_metadata(self):
        package = _package(
            "https://github.com/HDFGroup/hdf5.git", stars=993, forks=300,
            owner_avatar_url="https://a/h.png", homepage="https://hdfgroup.org", description="d",
            created_at="2017-01-01T00:00:00Z", default_branch="develop", language="C",
            license={"spdx_id": "BSD-3-Clause", "name": "BSD 3-Clause", "key": "bsd-3-clause"},
            topics=["hdf5", "io"], parent="up/hdf5", mentionable_users=150,
            pull_requests={"open": 1, "closed": 2, "merged": 3}, issues={"open": 4, "closed": 5},
            cdash="https://cdash/x",
        )
        assert build_repos_info({"hdf5": package})["data"] == {"HDFGroup/hdf5": {
            "nameWithOwner": "HDFGroup/hdf5",
            "name": "hdf5",
            "owner": {"login": "HDFGroup", "avatarUrl": "https://a/h.png"},
            "url": "https://github.com/HDFGroup/hdf5",
            "homepageUrl": "https://hdfgroup.org",
            "description": "d",
            "createdAt": "2017-01-01T00:00:00Z",
            "defaultBranchRef": {"name": "develop"},
            "licenseInfo": {"name": "BSD 3-Clause", "spdxId": "BSD-3-Clause",
                            "url": "http://choosealicense.com/licenses/bsd-3-clause/"},
            "primaryLanguage": {"name": "C"},
            "repositoryTopics": {"totalCount": 2},
            "parent": {"nameWithOwner": "up/hdf5", "name": "hdf5", "owner": {"login": "up"}},
            "stargazers": {"totalCount": 993},
            "forks": {"totalCount": 300},
            "mentionableUsers": {"totalCount": 150},
            "pullRequests_Open": {"totalCount": 1},
            "pullRequests_Closed": {"totalCount": 2},
            "pullRequests_Merged": {"totalCount": 3},
            "issues_Open": {"totalCount": 4},
            "issues_Closed": {"totalCount": 5},
            "published": True,
            "cdash": "https://cdash/x",
        }}

    def test_sparse_metadata(self):
        entry = build_repos_info({"p": _package("https://gitlab.com/g/sub/p", license={"name": "Custom"})})["data"]["g/sub/p"]
        assert entry["owner"] == {"login": "g/sub", "avatarUrl": None}
        assert entry["licenseInfo"]["url"] is None
        assert entry["primaryLanguage"] is None
        assert entry["parent"] is None
        assert entry["issues_Open"] == {"totalCount": None}
        assert "cdash" not in entry

    def test_unpublished_is_flagged(self):
        entry = build_repos_info({"hdf5": _package(published=False)})["data"]["Org/hdf5"]
        assert entry["published"] is False

    def test_package_without_repo_url_is_left_out(self):
        assert build_repos_info({"hdf5": {"metadata": {}}}) == {"data": {}}

    def test_dependencies_keyed_by_repo_path(self):
        deps = {"languages": {"nodes": []}, "dependencyGraphManifests": {"nodes": []}}
        packages = {
            "hdf5": {**_package("https://github.com/HDFGroup/hdf5"), "dependencies": deps},
            "thing": {**_package("https://gitlab.com/g/sub/thing.git"), "dependencies": deps},
            "none": {**_package("https://gitlab.com/g/none"), "dependencies": None},
        }
        assert set(build_dependencies(packages)["data"]) == {"HDFGroup/hdf5", "g/sub/thing"}

    def test_spack_keyed_by_spack_name(self):
        packages = {
            "hdf5": {**_package(), "spack": {"name": "hdf5", "dependencies": {"zlib": {}}, "dependents": {"netcdf-c": {}}}},
            "leaf": {**_package(), "spack": {"name": "leaf", "dependencies": {}, "dependents": {"x": {}}}},
            "absent": {**_package(), "spack": None},
            "gap": _package(),
        }
        dependencies, dependents = build_spack(packages)
        assert dependencies == {"hdf5": {"zlib": {}}}
        assert dependents == {"hdf5": {"netcdf-c": {}}, "leaf": {"x": {}}}


class TestMain:
    def test_writes_all_four_files(self, tmp_path):
        data = tmp_path / "data"
        _write(data, datetime.now(timezone.utc).strftime("%Y%m%d"), "hdf5", {"stars": 3},
               dependencies={"languages": {"nodes": []}},
               spack={"name": "hdf5", "dependencies": {"zlib": {}}, "dependents": {}})
        out = tmp_path / "out"
        assert main(["--data-dir", str(data), "--output", str(out)]) == 0
        assert json.loads((out / "intReposInfo.json").read_text())["data"]["Org/hdf5"]["stargazers"] == {"totalCount": 3}
        assert "Org/hdf5" in json.loads((out / "intRepos_Dependencies.json").read_text())["data"]
        assert json.loads((out / "spackPackageDependencies.json").read_text()) == {"hdf5": {"zlib": {}}}
        assert json.loads((out / "spackPackageDependents.json").read_text()) == {}

    def test_no_metrics_writes_nothing(self, tmp_path):
        data = tmp_path / "data"
        data.mkdir()
        out = tmp_path / "out"
        assert main(["--data-dir", str(data), "--output", str(out)]) == 1
        assert not out.exists()
