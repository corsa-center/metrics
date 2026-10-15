"""Unit tests for MetricsOrchestrator.prepare_software_list's group slicing.

The round-robin group split exists because a full-portfolio run's GitHub
API request volume is already close to the hourly quota before any
retries -- see the config/orchestrator.yaml rate_limiting comments and the
1e6c834/ccc561d commits. Splitting the catalog into N slices and running
one per scheduled invocation caps what any single run attempts.
"""

import asyncio
from unittest.mock import patch, AsyncMock

import pytest

from orchestrator import MetricsOrchestrator


def _catalog(names):
    return {
        name: {"name": name, "repo_url": f"https://github.com/org/{name}"}
        for name in names
    }


@pytest.fixture
def orch():
    o = MetricsOrchestrator(config_path="config/orchestrator.yaml")
    return o


async def _prepare(orch, catalog, **kwargs):
    with patch.object(orch, "load_software_catalog", new=AsyncMock(return_value=catalog)):
        result = await orch.prepare_software_list(**kwargs)
        return set(result[1])


class TestNoGrouping:
    def test_group_count_absent_returns_everything(self, orch):
        result = asyncio.run(_prepare(orch, _catalog(["a", "b", "c"])))
        assert len(result) == 3

    def test_group_count_of_one_returns_everything(self, orch):
        result = asyncio.run(_prepare(orch, _catalog(["a", "b", "c"]), group=0, group_count=1))
        assert len(result) == 3


class TestGrouping:
    def test_splits_into_equal_ish_slices(self, orch):
        catalog = _catalog([f"pkg{i}" for i in range(10)])
        group0 = asyncio.run(_prepare(orch, catalog, group=0, group_count=2))
        group1 = asyncio.run(_prepare(orch, catalog, group=1, group_count=2))
        assert len(group0) == 5
        assert len(group1) == 5

    def test_groups_are_disjoint_and_cover_everything(self, orch):
        catalog = _catalog([f"pkg{i}" for i in range(11)])  # odd count
        group0 = asyncio.run(_prepare(orch, catalog, group=0, group_count=2))
        group1 = asyncio.run(_prepare(orch, catalog, group=1, group_count=2))
        # names0 = {p["name"] for p in group0}
        # names1 = {p["name"] for p in group1}
        assert group0.isdisjoint(group1)
        assert group0 | group1 == {f"pkg{i}" for i in range(11)}

    def test_slicing_is_stable_regardless_of_catalog_order(self, orch):
        names = [f"pkg{i}" for i in range(10)]
        forward = asyncio.run(_prepare(orch, _catalog(names), group=0, group_count=2))
        backward = asyncio.run(_prepare(orch, _catalog(list(reversed(names))), group=0, group_count=2))
        assert forward == backward

    def test_filter_software_bypasses_grouping(self, orch):
        # Targeting one package by name should never depend on which
        # group it happens to fall in.
        catalog = _catalog(["kokkos", "hdf5"])
        result = asyncio.run(_prepare(
            orch, catalog, filter_software="kokkos", group=1, group_count=2
        ))
        assert len(result) == 1
        assert "kokkos" in result

class TestProcessedPackagesFilter:
    def test_processed_packages_filter(self, orch):
        catalog = _catalog([f"pkg{i}" for i in range(11)])
        result = asyncio.run(_prepare(orch, catalog, processed_packages={'pkg4': 4, 'pkg5': 5, 'pkg6': 6}))
        assert len(result) == 8

    def test_max_packages_limits_processed_packages_filter(self, orch):
        catalog = _catalog([f"pkg{i}" for i in range(11)])
        result = asyncio.run(_prepare(orch, catalog, max_packages=3, processed_packages={'pkg4': 4, 'pkg5': 5, 'pkg6': 6}))
        assert len(result) == 3

class TestMaxPackages:
    def test_none_keeps_everything(self, orch):
        result = asyncio.run(_prepare(orch, _catalog(["a", "b", "c"]), max_packages=None))
        assert len(result) == 3

    def test_a_limit_truncates(self, orch):
        result = asyncio.run(_prepare(orch, _catalog(["a", "b", "c"]), max_packages=2))
        assert len(result) == 2

    def test_zero_means_none_not_unlimited(self, orch):
        result = asyncio.run(_prepare(orch, _catalog(["a", "b", "c"]), max_packages=0))
        assert result == set()


class TestSingleRepo:
    """--repo/--repo-type/--name scan one repository without a catalog, so
    an individual maintainer can run this against their own project."""

    def test_builds_a_one_entry_catalog_without_the_catalog_url(self, orch):
        async def go():
            # Patched to blow up: the catalog must not be fetched at all.
            with patch.object(orch, "load_software_catalog", new=AsyncMock(side_effect=AssertionError)):
                return await orch.prepare_software_list(
                    repo="https://github.com/HDFGroup/hdf5", repo_type="github", name="HDF5"
                )

        catalog, software_list = asyncio.run(go())
        assert software_list == ["HDF5"]
        assert catalog == {"HDF5": {
            "name": "HDF5",
            "repo_url": "https://github.com/HDFGroup/hdf5",
            "repo_type": "github",
        }}

    def test_a_repo_without_a_name_is_rejected(self, orch):
        # The name keys both the catalog and the metrics output, so a None
        # would otherwise travel all the way to a `None-metrics/` directory.
        with pytest.raises(ValueError, match="name is required"):
            asyncio.run(orch.prepare_software_list(
                repo="https://github.com/HDFGroup/hdf5", repo_type="github"
            ))


class TestCatalogFetchFailure:
    def test_unreachable_catalog_raises_instead_of_returning_empty(self, orch):
        # An empty dict made a scheduled run report success with 0 packages.
        with patch.object(orch, "_fetch_catalog_files", return_value=None):
            with pytest.raises(RuntimeError, match="software catalog"):
                asyncio.run(orch.load_software_catalog())
