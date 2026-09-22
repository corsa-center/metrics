"""Unit tests for MetricsOrchestrator's catalog-identity preflight.

See METRIC_BLIND_SPOTS.md class F13: a catalog entry can point at a
renamed, deleted, or mistranscribed repository (RAJA-llnl/RAJA and vtk/vtk
both 404; the real locations are LLNL/RAJA and Kitware/VTK). Collecting
against it anyway means every collector's own 404s read as a confirmed
absence per file/API call, adding up to a full battery of confident zeros
instead of one clear "this repository doesn't exist".
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator import MetricsOrchestrator


@pytest.fixture
def orchestrator():
    return MetricsOrchestrator(config_path="config/orchestrator.yaml")


def _resp(status_code):
    r = MagicMock()
    r.status_code = status_code
    return r


class TestConfirmRepoExists:
    def test_404_is_confirmed_missing(self, orchestrator):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(404))
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = asyncio.run(orchestrator._confirm_repo_exists("RAJA-llnl/RAJA"))
        assert result is False

    def test_200_confirms_it_exists(self, orchestrator):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(200))
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = asyncio.run(orchestrator._confirm_repo_exists("LLNL/RAJA"))
        assert result is True

    def test_network_exception_fails_open(self, orchestrator):
        # A hiccup here must not silently drop a perfectly valid package --
        # the per-collector gap handling is the right tool for that
        # uncertainty, not a hard skip at this preflight stage.
        with patch("httpx.AsyncClient", side_effect=Exception("boom")):
            result = asyncio.run(orchestrator._confirm_repo_exists("HDFGroup/hdf5"))
        assert result is True

    def test_rate_limited_403_fails_open(self, orchestrator):
        # Not a confirmed absence -- only a clean 404 is.
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(403))
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = asyncio.run(orchestrator._confirm_repo_exists("HDFGroup/hdf5"))
        assert result is True


class TestCollectAllMetricsSkipsMissingRepo:
    def _package(self, repository="RAJA-llnl/RAJA"):
        return {
            "name": "RAJA",
            "repository": repository,
            "repo_url": f"https://github.com/{repository}",
        }

    def test_confirmed_missing_repo_skips_the_three_dimensions(self, orchestrator):
        async def go():
            with patch.object(orchestrator, "_load_package_config", return_value={}), \
                 patch.object(orchestrator, "_fetch_project_config", new=AsyncMock(return_value={})), \
                 patch.object(orchestrator, "_confirm_repo_exists", new=AsyncMock(return_value=False)), \
                 patch.object(orchestrator, "collect_impact_dimension") as impact, \
                 patch.object(orchestrator, "collect_ecosystem_dimension") as eco, \
                 patch.object(orchestrator, "collect_quality_dimension") as qual:
                return await orchestrator.collect_all_metrics(self._package())

        result = asyncio.run(go())
        # None of the three dimension collectors should have been called at all.
        assert result["dimensions"]["impact"]["score"] == 0.0
        assert result["dimensions"]["ecosystem"]["score"] == 0.0
        assert result["dimensions"]["quality"]["score"] == 0.0

    def test_confirmed_existing_repo_still_collects_normally(self, orchestrator):
        fake_dim = {"dimension": "x", "score": 42.0, "max_score": 100.0}

        async def go():
            with patch.object(orchestrator, "_load_package_config", return_value={}), \
                 patch.object(orchestrator, "_fetch_project_config", new=AsyncMock(return_value={})), \
                 patch.object(orchestrator, "_confirm_repo_exists", new=AsyncMock(return_value=True)), \
                 patch.object(orchestrator, "collect_impact_dimension", new=AsyncMock(return_value=fake_dim)), \
                 patch.object(orchestrator, "collect_ecosystem_dimension", new=AsyncMock(return_value=fake_dim)), \
                 patch.object(orchestrator, "collect_quality_dimension", new=AsyncMock(return_value=fake_dim)):
                return await orchestrator.collect_all_metrics(self._package("HDFGroup/hdf5"))

        result = asyncio.run(go())
        assert result["dimensions"]["impact"]["score"] == 42.0

    def test_non_github_repo_check_takes_priority_over_existence_check(self, orchestrator):
        # A GitLab repo shouldn't trigger a GitHub existence lookup at all.
        async def go():
            with patch.object(orchestrator, "_load_package_config", return_value={}), \
                 patch.object(orchestrator, "_fetch_project_config", new=AsyncMock(return_value={})), \
                 patch.object(orchestrator, "_confirm_repo_exists", new=AsyncMock(return_value=True)) as confirm:
                pkg = {
                    "name": "GitLabThing",
                    "repository": "owner/thing",
                    "repo_url": "https://gitlab.com/owner/thing",
                }
                await orchestrator.collect_all_metrics(pkg)
                confirm.assert_not_called()

        asyncio.run(go())
