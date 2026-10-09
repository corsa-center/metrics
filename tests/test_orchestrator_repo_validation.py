"""Unit tests for MetricsOrchestrator's catalog-identity preflight.

See METRIC_BLIND_SPOTS.md class F13: a catalog entry can point at a
renamed, deleted, or mistranscribed repository. Collecting against it
anyway means every collector's own 404s read as a confirmed absence,
instead of one clear "this repository doesn't exist".
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge.base import COLLECTION_GAP
from forge.gitlab import GitLabForge
from orchestrator import MetricsOrchestrator
from tests.fakes import FakeForge


@pytest.fixture
def orchestrator():
    return MetricsOrchestrator(config_path="config/orchestrator.yaml")


class TestConfirmRepoExists:
    """Existence comes from the package's own forge: repo_info() is None
    only for a confirmed 404, and a gap (rate limit, network error) is not
    a confirmed absence."""

    def _confirm(self, orchestrator, repo_info, url="https://github.com/LLNL/RAJA"):
        forge = FakeForge()
        if isinstance(repo_info, Exception):
            forge.repo_info = AsyncMock(side_effect=repo_info)
        else:
            forge.repo_info_data = repo_info
        return asyncio.run(orchestrator._confirm_repo_exists(forge, url))

    def test_404_is_confirmed_missing(self, orchestrator):
        assert self._confirm(orchestrator, None, "https://github.com/RAJA-llnl/RAJA") is False

    def test_found_confirms_it_exists(self, orchestrator):
        assert self._confirm(orchestrator, {"stars": 1}) is True

    def test_network_exception_fails_open(self, orchestrator):
        # A hiccup here must not silently drop a perfectly valid package --
        # the per-collector gap handling is the right tool for that
        # uncertainty, not a hard skip at this preflight stage.
        assert self._confirm(orchestrator, Exception("boom")) is True

    def test_gap_fails_open(self, orchestrator):
        # Not a confirmed absence -- only a clean 404 is.
        assert self._confirm(orchestrator, COLLECTION_GAP) is True

    def test_unparseable_url_is_missing(self, orchestrator):
        assert self._confirm(orchestrator, {"stars": 1}, "not-a-url") is False


class TestCollectAllMetricsSkipsMissingRepo:
    def _package(self, repository="RAJA-llnl/RAJA"):
        return {
            "name": f"{repository}",
            "repo_type": "github",
            "repo_url": f"https://github.com/{repository}",
        }

    def test_confirmed_missing_repo_skips_the_three_dimensions(self, orchestrator):
        async def go():
            with patch.object(orchestrator, "_fetch_package_config", new=AsyncMock(return_value={})), \
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
            with patch.object(orchestrator, "_fetch_package_config", new=AsyncMock(return_value={})), \
                 patch.object(orchestrator, "_confirm_repo_exists", new=AsyncMock(return_value=True)), \
                 patch.object(orchestrator, "collect_impact_dimension", new=AsyncMock(return_value=fake_dim)), \
                 patch.object(orchestrator, "collect_ecosystem_dimension", new=AsyncMock(return_value=fake_dim)), \
                 patch.object(orchestrator, "collect_quality_dimension", new=AsyncMock(return_value=fake_dim)):
                return await orchestrator.collect_all_metrics(self._package("HDFGroup/hdf5"))

        result = asyncio.run(go())
        assert result["dimensions"]["impact"]["score"] == 42.0

    def test_unsupported_platform_skips_before_the_existence_check(self, orchestrator):
        # A host no forge supports shouldn't trigger an existence lookup at all.
        async def go():
            with patch.object(orchestrator, "_fetch_package_config", new=AsyncMock(return_value={})), \
                 patch.object(orchestrator, "_confirm_repo_exists", new=AsyncMock(return_value=True)) as confirm, \
                 patch.object(orchestrator, "collect_impact_dimension") as impact:
                pkg = {"name": "Elsewhere", "repo_type": "bitbucket",
                       "repo_url": "https://bitbucket.org/owner/thing"}
                result = await orchestrator.collect_all_metrics(pkg)
                confirm.assert_not_called()
                impact.assert_not_called()
                return result

        result = asyncio.run(go())
        assert result["dimensions"]["quality"]["score"] == 0.0

    def test_gitlab_repo_is_checked_and_collected(self, orchestrator):
        fake_dim = {"dimension": "x", "score": 42.0, "max_score": 100.0}

        async def go():
            with patch.object(orchestrator, "_fetch_package_config", new=AsyncMock(return_value={})), \
                 patch.object(orchestrator, "_confirm_repo_exists", new=AsyncMock(return_value=True)) as confirm, \
                 patch.object(orchestrator, "collect_impact_dimension", new=AsyncMock(return_value=fake_dim)), \
                 patch.object(orchestrator, "collect_ecosystem_dimension", new=AsyncMock(return_value=fake_dim)), \
                 patch.object(orchestrator, "collect_quality_dimension", new=AsyncMock(return_value=fake_dim)):
                pkg = {"name": "GitLabThing", "repo_type": "gitlab",
                       "repo_url": "https://gitlab.com/owner/thing"}
                result = await orchestrator.collect_all_metrics(pkg)
                assert isinstance(confirm.call_args.args[0], GitLabForge)
                return result

        assert asyncio.run(go())["dimensions"]["impact"]["score"] == 42.0
