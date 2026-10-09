"""Unit tests for DeploymentEnvironmentCollector (CASS Section 4.3.5)."""

import pytest

from collectors.quality.deployment_environments import (
    DeploymentEnvironmentCollector, _RUNNER_FAMILIES,
)
from tests.fakes import FakeForge


@pytest.fixture
def collector():
    return DeploymentEnvironmentCollector(FakeForge())


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


class TestArchitectureDetection:
    @pytest.mark.parametrize("text,expected", [
        ("runs-on: ubuntu-24.04-arm", "ARM64"),   # version prefix, not letters
        ("name: arm-main", "ARM64"),
        ("os: [aarch64]", "ARM64"),
        ("os: [ppc64le]", "POWER"),
        ("arch: riscv64", "RISC-V"),
        ("container: s390x/ubuntu", "s390x"),
    ])
    def test_detects_architecture(self, text, expected):
        from collectors.quality.deployment_environments import _ARCH_PATTERNS
        assert [a for a, p in _ARCH_PATTERNS.items() if p.search(text)] == [expected]

    @pytest.mark.parametrize("text", [
        "runs-on: ubuntu-latest", "runs-on: windows-2022", "job: warm-up", "swarm-node",
        # Intel macOS runners and a workflow filename aren't Apple Silicon.
        "runs-on: macos-13", "runs-on: macos-15-intel", "runs-on: macos-14-large",
        "uses: ./.github/workflows/macos-latest.yml",
    ])
    def test_no_false_positives(self, text):
        from collectors.quality.deployment_environments import _ARCH_PATTERNS
        assert [a for a, p in _ARCH_PATTERNS.items() if p.search(text)] == []

    @pytest.mark.parametrize("text", [
        "runs-on: macos-latest", "runs-on: macos-14", "os: [macos-15, windows-latest]",
        "runs-on: macos-26", "runs-on: macos-13-xlarge",
        "os: [ubuntu-latest, macos-latest, macos-15-intel]",
    ])
    def test_apple_silicon_macos_runners_are_arm64(self, text):
        from collectors.quality.deployment_environments import _ARCH_PATTERNS
        assert _ARCH_PATTERNS["ARM64"].search(text)


class TestAcceleratorDetection:
    def _hits(self, text):
        from collectors.quality.deployment_environments import _ACCELERATOR_PATTERNS
        return sorted(a for a, p in _ACCELERATOR_PATTERNS.items() if p.search(text))

    @pytest.mark.parametrize("text,expected", [
        ('SPEC: "%gcc +mpi+cuda cuda_arch=70"', "NVIDIA GPU (CUDA)"),
        ("cmake -DKokkos_ENABLE_CUDA=ON ..", "NVIDIA GPU (CUDA)"),
        ("-DCMAKE_CUDA_ARCHITECTURES=80", "NVIDIA GPU (CUDA)"),
        ("image: nvidia/cuda:12.4.0-devel-ubuntu22.04", "NVIDIA GPU (CUDA)"),
        ("SPEC: +rocm amdgpu_target=gfx90a", "AMD GPU (ROCm/HIP)"),
        ("-DENABLE_HIP=ON", "AMD GPU (ROCm/HIP)"),
        ("-DAMReX_GPU_BACKEND=HIP", "AMD GPU (ROCm/HIP)"),
        ("-DENABLE_SYCL=ON", "Intel GPU (SYCL)"),
        # Self-hosted GPU runner labels and Kokkos architecture names.
        ("target-runner-labels: \"['self-hosted', 'gpu:A100']\"", "NVIDIA GPU (CUDA)"),
        ("genconfig-string: rhel_cuda-12-gcc_release_Ampere80_no-asan", "NVIDIA GPU (CUDA)"),
        ("-DKokkos_ARCH_VOLTA70=ON", "NVIDIA GPU (CUDA)"),
        ("-DKokkos_ARCH_AMD_GFX90A=ON", "AMD GPU (ROCm/HIP)"),
        ("runs-on: [self-hosted, MI250X]", "AMD GPU (ROCm/HIP)"),
        ("-DKokkos_ARCH_INTEL_PVC=ON", "Intel GPU (SYCL)"),
    ])
    def test_detects_accelerator(self, text, expected):
        assert self._hits(text) == [expected]

    @pytest.mark.parametrize("text", [
        "export CUDA_LAUNCH_BLOCKING=1", "name: cuda-build", "# TODO: add a ROCm job",
        "-DENABLE_CUDA=OFF", "~cuda", "runs-on: ubuntu-latest",
        "version: 100", "Adam89", "Volta700", "MI3",
    ])
    def test_no_false_positives(self, text):
        assert self._hits(text) == []


class TestArchitectureAndDocs:
    def test_extra_architecture_passes(self, collector):
        s = collector._calculate_score(
            {"Linux": ["ubuntu-latest"]}, ["ARM64"], [])
        info = s["sub_scores"]["architecture_compatibility"]
        assert info["passing"]
        assert info["value"] == "x86-64 plus ARM64"

    def test_x86_only_fails(self, collector):
        # x86-64 is the implicit default for every standard runner, so naming
        # it proves nothing about portability.
        s = collector._calculate_score({"Linux": ["ubuntu-latest"]}, [], [])
        info = s["sub_scores"]["architecture_compatibility"]
        assert not info["passing"]
        assert info["value"] == "x86-64 only"

    def test_accelerator_alone_passes(self, collector):
        s = collector._calculate_score(
            {"Linux": ["ubuntu-latest"]}, [], [], ["AMD GPU (ROCm/HIP)"])
        info = s["sub_scores"]["architecture_compatibility"]
        assert info["passing"]
        assert info["value"] == "x86-64 only; GPU targets: AMD GPU (ROCm/HIP)"

    def test_accelerator_found_without_github_runners(self, collector):
        # GPU testing configured only in GitLab CI, no GitHub-hosted runners.
        info = collector._calculate_score({}, [], [], ["NVIDIA GPU (CUDA)"])["sub_scores"][
            "architecture_compatibility"]
        assert info["passing"]
        assert "NVIDIA GPU (CUDA)" in info["value"]

    def test_platform_documentation_threshold(self, collector):
        one = collector._calculate_score({}, [], ["Windows"])
        two = collector._calculate_score({}, [], ["Linux", "Windows"])
        assert not one["sub_scores"]["platform_documentation"]["passing"]
        assert two["sub_scores"]["platform_documentation"]["passing"]

    def test_no_platforms_named(self, collector):
        info = collector._calculate_score({}, [], [])["sub_scores"]["platform_documentation"]
        assert not info["passing"]
        assert "No supported platforms" in info["value"]

    def test_max_score_is_three(self, collector):
        assert collector._calculate_score({}, [], [])["max_score"] == 3


class TestScoring:
    def test_single_family_fails(self, collector):
        s = collector._calculate_score({"Linux": ["ubuntu-latest"]})
        assert not s["sub_scores"]["deployment_environment_testing"]["passing"]

    def test_two_families_pass(self, collector):
        s = collector._calculate_score(
            {"Linux": ["ubuntu-latest"], "Windows": ["windows-latest"]}
        )
        assert s["sub_scores"]["deployment_environment_testing"]["passing"]

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


class TestInstallDocPattern:
    @pytest.mark.parametrize("path", [
        "INSTALL", "INSTALL.md", "doc/shared/sundials/Install.rst", "docs/installation.md",
        "docs/building.rst", "doc/install_guide/source/Install_link.rst", "BUILD.md",
        "src/doc/building_visit/Building_Directly_With_CMake.rst",
    ])
    def test_install_guides_match(self, path):
        import re
        from collectors.quality.deployment_environments import _INSTALL_DOC
        assert re.search(_INSTALL_DOC, path, re.I), path

    @pytest.mark.parametrize("path", [
        "src/install.c", "scripts/install.sh", "cmake/SundialsInstall.cmake", "docs/uninstalling.md",
        "lib/spack/docs/build_settings.rst", "docs/build_requirements.txt", "docs/build_and_release.rst",
    ])
    def test_non_guides_do_not_match(self, path):
        import re
        from collectors.quality.deployment_environments import _INSTALL_DOC
        assert not re.search(_INSTALL_DOC, path, re.I), path


@pytest.mark.parametrize("path,ok", [
    ("llvm/docs/GettingStarted.rst", True), ("docs/source/quick_start.rst", True),
    ("docs/supported_platforms.md", True), ("docs/system-requirements.md", True),
    ("requirements.txt", False), ("docs/requirements.txt", False), ("src/platforms.cpp", False),
])
def test_platform_guides_match(path, ok):
    import re
    from collectors.quality.deployment_environments import _PLATFORM_GUIDE
    assert bool(re.search(_PLATFORM_GUIDE, path, re.I)) is ok


class TestUnstatedRunners:
    def test_self_hosted_only_is_unmeasured(self):
        from collectors.quality.deployment_environments import DeploymentEnvironmentCollector
        s = DeploymentEnvironmentCollector(FakeForge())._calculate_score({}, unstated_ci=True)
        row = s["sub_scores"]["deployment_environment_testing"]
        assert row["unmeasured"] is True
        assert row["value"] == "CI runs on self-hosted or non-GitHub runners whose OS isn't stated"
        assert s["max_score"] == 2

    def test_hosted_linux_plus_self_hosted(self):
        from collectors.quality.deployment_environments import DeploymentEnvironmentCollector
        s = DeploymentEnvironmentCollector(FakeForge())._calculate_score({"Linux": ["ubuntu-latest"]}, unstated_ci=True)
        assert s["sub_scores"]["deployment_environment_testing"]["value"].startswith("1 environment: Linux; other CI runs")

    def test_no_ci_at_all_is_still_a_fail(self):
        from collectors.quality.deployment_environments import DeploymentEnvironmentCollector
        row = DeploymentEnvironmentCollector(FakeForge())._calculate_score({})["sub_scores"]["deployment_environment_testing"]
        assert not row.get("unmeasured") and row["passing"] is False

    @pytest.mark.parametrize("text,hit", [
        ("runs-on: [self-hosted, cpu_intel]", True), ("runs-on: self-hosted", True),
        ("runs-on:\n      - self-hosted\n      - gpu", True), ("runs-on: ubuntu-latest", False),
    ])
    def test_self_hosted_pattern(self, text, hit):
        from collectors.quality.deployment_environments import _SELF_HOSTED
        assert bool(_SELF_HOSTED.search(text)) is hit


class TestCollectRunnerKinds:
    def _collect(self, workflow_text, tree_paths=()):
        import asyncio
        from unittest.mock import AsyncMock, patch
        from collectors.ecosystem.base import RepoTree
        from collectors.quality.deployment_environments import DeploymentEnvironmentCollector
        c = DeploymentEnvironmentCollector(FakeForge())
        tree = RepoTree(FakeForge(), "o/r", [".github/workflows/ci.yml", *tree_paths], truncated=False)
        with patch.object(c, "_list_workflows", new=AsyncMock(return_value=[{"name": "ci.yml", "path": ".github/workflows/ci.yml"}])), \
             patch.object(c, "_read_platform_docs", new=AsyncMock(return_value="")), \
             patch.object(RepoTree, "fetch", new=AsyncMock(return_value=tree)), \
             patch.object(c, "_read_workflow", new=AsyncMock(return_value=workflow_text)):
            return asyncio.run(c.collect({"name": "r", "repo_url": "https://github.com/o/r"}))

    def test_matrix_of_standard_runners_is_stated(self):
        r = self._collect("runs-on: ${{ matrix.os }}\nstrategy:\n  matrix:\n    os: [ubuntu-latest]\n")
        row = r["overall_score"]["sub_scores"]["deployment_environment_testing"]
        assert not row.get("unmeasured") and row["value"] == "1 environment: Linux"

    def test_hardware_labelled_self_hosted_is_unmeasured(self):
        r = self._collect("runs-on: [self-hosted, gpu_amd]\n")
        assert r["overall_score"]["sub_scores"]["deployment_environment_testing"]["unmeasured"] is True

    def test_self_hosted_os_labels_count(self):
        r = self._collect("jobs:\n a:\n  runs-on: [self-hosted, macOS]\n b:\n  runs-on: ubuntu-latest\n")
        row = r["overall_score"]["sub_scores"]["deployment_environment_testing"]
        assert row["passing"] and "macOS" in row["value"]
