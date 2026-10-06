import pytest

from collectors import rate_limit


@pytest.fixture(autouse=True)
def _unpaced_search_limiter(monkeypatch):
    """Tests mock the network, so the process-wide search limiter's real-time
    spacing would only make the suite slow. Its own behaviour is tested on
    separate instances in test_rate_limit.py."""
    monkeypatch.setattr(rate_limit.search_limiter, "_interval", 0)
    monkeypatch.setattr(rate_limit.search_limiter, "_times", [])


@pytest.fixture(autouse=True)
def _no_shared_index_downloads(monkeypatch):
    """Spack's recipe index and the E4S image list are downloaded once per
    run and cached per process; start every test with both empty so nothing
    reaches the network. Tests of the lookups set their own."""
    from collectors.ecosystem import collaboration
    from collectors.quality import accessibility
    monkeypatch.setattr(collaboration, "_spack_by_repo", {})
    monkeypatch.setattr(accessibility, "_e4s_specs", set())
