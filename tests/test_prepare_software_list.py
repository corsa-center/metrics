"""Unit tests for MetricsOrchestrator.prepare_software_list's group slicing.

The round-robin group split exists because a full-portfolio run's GitHub
API request volume is already close to the hourly quota before any
retries -- see the config/orchestrator.yaml rate_limiting comments and the
1e6c834/ccc561d commits. Splitting the catalog into N slices and running
one per scheduled invocation caps what any single run attempts.
"""

from unittest.mock import patch

import pytest

from orchestrator import MetricsOrchestrator


def _catalog(names):
    return {
        name: {"name": name, "url": f"https://github.com/org/{name}"}
        for name in names
    }


@pytest.fixture
def orch():
    o = MetricsOrchestrator.__new__(MetricsOrchestrator)
    return o


def _prepare(orch, catalog, **kwargs):
    with patch.object(orch, "load_software_catalog", return_value=catalog):
        return orch.prepare_software_list(**kwargs)


class TestNoGrouping:
    def test_group_count_absent_returns_everything(self, orch):
        result = _prepare(orch, _catalog(["a", "b", "c"]))
        assert len(result) == 3

    def test_group_count_of_one_returns_everything(self, orch):
        result = _prepare(orch, _catalog(["a", "b", "c"]), group=0, group_count=1)
        assert len(result) == 3


class TestGrouping:
    def test_splits_into_equal_ish_slices(self, orch):
        catalog = _catalog([f"pkg{i}" for i in range(10)])
        group0 = _prepare(orch, catalog, group=0, group_count=2)
        group1 = _prepare(orch, catalog, group=1, group_count=2)
        assert len(group0) == 5
        assert len(group1) == 5

    def test_groups_are_disjoint_and_cover_everything(self, orch):
        catalog = _catalog([f"pkg{i}" for i in range(11)])  # odd count
        group0 = _prepare(orch, catalog, group=0, group_count=2)
        group1 = _prepare(orch, catalog, group=1, group_count=2)
        names0 = {p["repository"] for p in group0}
        names1 = {p["repository"] for p in group1}
        assert names0.isdisjoint(names1)
        assert names0 | names1 == {f"pkg{i}" for i in range(11)}

    def test_slicing_is_stable_regardless_of_catalog_order(self, orch):
        names = [f"pkg{i}" for i in range(10)]
        forward = _prepare(orch, _catalog(names), group=0, group_count=2)
        backward = _prepare(orch, _catalog(list(reversed(names))), group=0, group_count=2)
        assert [p["repository"] for p in forward] == [p["repository"] for p in backward]

    def test_filter_software_bypasses_grouping(self, orch):
        # Targeting one package by name should never depend on which
        # group it happens to fall in.
        catalog = _catalog(["kokkos", "hdf5"])
        result = _prepare(
            orch, catalog, filter_software="kokkos", group=1, group_count=2
        )
        assert len(result) == 1
        assert result[0]["repository"] == "kokkos"
