"""Unit tests for MetricsOrchestrator's catalog-identity preflight.

See METRIC_BLIND_SPOTS.md class F13: a catalog entry can point at a
renamed, deleted, or mistranscribed repository. Collecting against it
anyway means every collector's own 404s read as a confirmed absence,
instead of one clear "this repository doesn't exist".
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge.base import COLLECTION_GAP, get_default_repo_info
from forge.gitlab import GitLabForge
from orchestrator import MetricsOrchestrator
from tests.fakes import FakeForge


@pytest.fixture
def orchestrator():
    return MetricsOrchestrator(config_path="config/orchestrator.yaml")


class TestGetRepoInfo:
    """_update_repo_info() returns True unless there is a network problem that is not a gap (rate limit, network error)
    """

    def _repo_info(self, orchestrator, repo_info, url="https://github.com/LLNL/RAJA"):
        forge = FakeForge()
        if isinstance(repo_info, Exception):
            forge.repo_info = AsyncMock(side_effect=repo_info)
        else:
            forge.repo_info_data = repo_info
        return asyncio.run(orchestrator._get_repo_info(forge, url))

    def test_repo_info_succeeds(self, orchestrator):
        assert bool(self._repo_info(orchestrator, {"stars": 1})) is True

    def test_unparseable_url_is_missing(self, orchestrator):
        assert bool(self._repo_info(orchestrator, {"stars": 1}, "not-a-url")) is False

    def test_unexpected_exception_is_a_gap_not_an_absence(self, orchestrator):
        # repo_info's helpers absorb 404s, rate limits and 5xx, so an
        # exception here is something else -- a malformed 200 body, say.
        # It must not read as "this repository doesn't exist".
        assert self._repo_info(orchestrator, RuntimeError("boom")) is COLLECTION_GAP

    def test_the_exception_is_logged(self, orchestrator, caplog):
        self._repo_info(orchestrator, RuntimeError("boom"))
        assert "boom" in caplog.text


class TestRepoMetadataOnThePackage:
    """_update_repo_info copies repo metadata onto the package, but only
    what the forge actually answered -- a failed lookup must not overwrite
    curated catalog values with defaults."""

    def _update(self, orchestrator, repo_info, **package_extra):
        package = {
            "name": "P",
            "repo_type": "github",
            "repo_url": "https://github.com/o/r",
            **package_extra,
        }
        with patch.object(orchestrator, "_get_repo_info", new=AsyncMock(return_value=repo_info)):
            collect, reason = asyncio.run(orchestrator._update_repo_info(package))
        return collect, reason, package

    def test_real_values_land_on_the_package(self, orchestrator):
        info = {**get_default_repo_info(), "description": "d", "language": "C"}
        _, _, package = self._update(orchestrator, info)
        assert package["description"] == "d"
        assert package["language"] == "C"

    def test_false_and_zero_are_recorded_not_dropped(self, orchestrator):
        # "not archived" and "empty repo" are real answers; the dashboard
        # can't tell them from "never looked" if the keys are absent.
        info = {**get_default_repo_info(), "archived": False, "disabled": False, "size_kb": 0}
        _, _, package = self._update(orchestrator, info)
        assert package["archived"] is False
        assert package["disabled"] is False
        assert package["size_kb"] == 0

    def test_a_gap_keeps_curated_catalog_values(self, orchestrator):
        _, _, package = self._update(
            orchestrator, COLLECTION_GAP,
            description="curated", homepage="https://example.org",
        )
        assert package["description"] == "curated"
        assert package["homepage"] == "https://example.org"

    def test_a_gap_writes_no_default_branch(self, orchestrator):
        # get_default_repo_info()'s default_branch is "HEAD", the one truthy
        # default; writing it for a repo we never reached is a made-up answer.
        _, _, package = self._update(orchestrator, COLLECTION_GAP)
        assert "default_branch" not in package

    def test_a_missing_repo_writes_nothing(self, orchestrator):
        collect, reason, package = self._update(orchestrator, None)
        assert collect is False
        assert "https://github.com/o/r" in reason
        assert set(package) == {"name", "repo_type", "repo_url"}

    def test_an_unsupported_platform_reports_why(self, orchestrator):
        package = {"name": "P", "repo_type": "bitbucket",
                   "repo_url": "https://bitbucket.org/o/r"}
        collect, reason = asyncio.run(orchestrator._update_repo_info(package))
        assert collect is False
        assert "not a supported platform" in reason


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
                 patch.object(orchestrator, "_get_repo_info", new=AsyncMock(return_value={})), \
                 patch.object(orchestrator, "collect_impact_dimension") as impact, \
                 patch.object(orchestrator, "collect_ecosystem_dimension") as eco, \
                 patch.object(orchestrator, "collect_quality_dimension") as qual:
                return await orchestrator.collect_all_metrics(self._package()), impact, eco, qual

        result, impact, eco, qual = asyncio.run(go())
        impact.assert_not_called()
        eco.assert_not_called()
        qual.assert_not_called()
        assert result["dimensions"]["impact"]["score"] == 0.0
        assert result["dimensions"]["ecosystem"]["score"] == 0.0
        assert result["dimensions"]["quality"]["score"] == 0.0

    def test_confirmed_existing_repo_still_collects_normally(self, orchestrator):
        fake_dim = {"dimension": "x", "score": 42.0, "max_score": 100.0}

        async def go():
            with patch.object(orchestrator, "_fetch_package_config", new=AsyncMock(return_value={})), \
                 patch.object(orchestrator, "_get_repo_info", new=AsyncMock(return_value=get_default_repo_info())), \
                 patch.object(orchestrator, "collect_impact_dimension", new=AsyncMock(return_value=fake_dim)), \
                 patch.object(orchestrator, "collect_ecosystem_dimension", new=AsyncMock(return_value=fake_dim)), \
                 patch.object(orchestrator, "collect_quality_dimension", new=AsyncMock(return_value=fake_dim)), \
                 patch.object(orchestrator, "collect_dashboard_info", new=AsyncMock(return_value={})):
                return await orchestrator.collect_all_metrics(self._package("HDFGroup/hdf5"))

        result = asyncio.run(go())
        assert result["dimensions"]["impact"]["score"] == 42.0

    def test_gap_still_collects_normally(self, orchestrator):
        fake_dim = {"dimension": "x", "score": 42.0, "max_score": 100.0}

        async def go():
            with patch.object(orchestrator, "_fetch_package_config", new=AsyncMock(return_value={})), \
                 patch.object(orchestrator, "_get_repo_info", new=AsyncMock(return_value=COLLECTION_GAP)), \
                 patch.object(orchestrator, "collect_impact_dimension", new=AsyncMock(return_value=fake_dim)), \
                 patch.object(orchestrator, "collect_ecosystem_dimension", new=AsyncMock(return_value=fake_dim)), \
                 patch.object(orchestrator, "collect_quality_dimension", new=AsyncMock(return_value=fake_dim)), \
                 patch.object(orchestrator, "collect_dashboard_info", new=AsyncMock(return_value={})):
                return await orchestrator.collect_all_metrics(self._package("HDFGroup/hdf5"))

        result = asyncio.run(go())
        assert result["dimensions"]["impact"]["score"] == 42.0

    def test_404_skips_collection(self, orchestrator):
        async def go():
            with patch.object(orchestrator, "_fetch_package_config", new=AsyncMock(return_value={})), \
                 patch.object(orchestrator, "_get_repo_info", new=AsyncMock(return_value=None)), \
                 patch.object(orchestrator, "collect_impact_dimension") as impact, \
                 patch.object(orchestrator, "collect_ecosystem_dimension") as eco, \
                 patch.object(orchestrator, "collect_quality_dimension") as qual:
                return await orchestrator.collect_all_metrics(self._package()), impact, eco, qual

        result, impact, eco, qual = asyncio.run(go())
        impact.assert_not_called()
        eco.assert_not_called()
        qual.assert_not_called()
        assert result["dimensions"]["impact"]["score"] == 0.0
        assert result["dimensions"]["ecosystem"]["score"] == 0.0
        assert result["dimensions"]["quality"]["score"] == 0.0

    def test_unsupported_platform_skips_before_the_existence_check(self, orchestrator):
        # A host no forge supports shouldn't trigger an existence lookup at all.
        async def go():
            with patch.object(orchestrator, "_fetch_package_config", new=AsyncMock(return_value={})), \
                 patch.object(orchestrator, "_get_repo_info", new=AsyncMock(return_value=get_default_repo_info())) as confirm, \
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
                 patch.object(orchestrator, "_get_repo_info", new=AsyncMock(return_value=get_default_repo_info())) as confirm, \
                 patch.object(orchestrator, "collect_impact_dimension", new=AsyncMock(return_value=fake_dim)), \
                 patch.object(orchestrator, "collect_ecosystem_dimension", new=AsyncMock(return_value=fake_dim)), \
                 patch.object(orchestrator, "collect_quality_dimension", new=AsyncMock(return_value=fake_dim)), \
                 patch.object(orchestrator, "collect_dashboard_info", new=AsyncMock(return_value={})):
                pkg = {"name": "GitLabThing", "repo_type": "gitlab",
                       "repo_url": "https://gitlab.com/owner/thing"}
                result = await orchestrator.collect_all_metrics(pkg)
                assert isinstance(confirm.call_args.args[0], GitLabForge)
                return result

        assert asyncio.run(go())["dimensions"]["impact"]["score"] == 42.0
