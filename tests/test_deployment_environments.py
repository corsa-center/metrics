"""Unit tests for DeploymentEnvironmentCollector (CASS Section 4.3.5)."""

import pytest

from collectors.quality.deployment_environments import (
    DeploymentEnvironmentCollector, _RUNNER_FAMILIES,
)


@pytest.fixture
def collector():
    return DeploymentEnvironmentCollector()


def _families(text):
    out = {}
    for family, pattern in _RUNNER_FAMILIES.items():
        hits = sorted({m.lower() for m in pattern.findall(text)})
        if hits:
            out[family] = hits
    return out


class TestRunnerDetection:
    def test_matches_literal_runs_on(self):
        assert _families("runs-on: ubuntu-latest") == {"Linux": ["ubuntu-latest"]}

    def test_matches_runners_declared_in_a_matrix(self):
        # The common real-world shape: runs-on indirects through the matrix.
        text = """
        runs-on: ${{ matrix.os }}
        strategy:
          matrix:
            os: [ubuntu-24.04, windows-2022, macos-15]
        """
        assert set(_families(text)) == {"Linux", "Windows", "macOS"}

    @pytest.mark.parametrize("label", ["macos-clang", "linux-oneapi", "ubuntu-gcc", "windows-msvc"])
    def test_toolchain_job_names_are_not_runners(self, label):
        # These appear in job names; treating them as runners made the reported
        # environment list wrong.
        assert _families(f"name: {label}") == {}

    def test_versioned_and_latest_both_count(self):
        assert _families("windows-11 windows-latest")["Windows"] == [
            "windows-11", "windows-latest"
        ]

    def test_point_versions(self):
        assert _families("ubuntu-22.04")["Linux"] == ["ubuntu-22.04"]


class TestScoring:
    def test_single_family_fails(self, collector):
        s = collector._calculate_score({"Linux": ["ubuntu-latest"]})
        assert not s["sub_scores"]["deployment_environment_testing"]["passing"]

    def test_two_families_pass(self, collector):
        s = collector._calculate_score(
            {"Linux": ["ubuntu-latest"], "Windows": ["windows-latest"]}
        )
        assert s["sub_scores"]["deployment_environment_testing"]["passing"]
        assert s["score"] == 1

    def test_no_families(self, collector):
        s = collector._calculate_score({})
        info = s["sub_scores"]["deployment_environment_testing"]
        assert not info["passing"]
        assert "No CI runner" in info["value"]
        assert info["detail"] is None

    def test_value_is_a_summary_not_a_label_dump(self, collector):
        s = collector._calculate_score({
            "Windows": ["windows-11", "windows-2022", "windows-latest"],
            "Linux": ["ubuntu-24.04", "ubuntu-latest"],
        })
        info = s["sub_scores"]["deployment_environment_testing"]
        assert info["value"] == "2 environments: Linux, Windows"
        # Individual runner labels are kept in os_families, not rendered.
        assert info["detail"] is None
        assert "ubuntu-24.04" not in info["value"]

    def test_single_environment_is_singular(self, collector):
        s = collector._calculate_score({"Linux": ["ubuntu-latest"]})
        assert s["sub_scores"]["deployment_environment_testing"]["value"] == (
            "1 environment: Linux"
        )


class TestEmptyResult:
    def test_invalid_url(self, collector):
        import asyncio
        r = asyncio.run(collector.collect({"name": "x", "repo_url": "nope"}))
        assert r["os_families"] == {}
        assert r["overall_score"]["score"] == 0
