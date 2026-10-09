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


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """Fail any test that reaches the real network. Collectors turn a
    transport error into COLLECTION_GAP, so a stray request would otherwise
    pass quietly (and slowly) instead of exposing a mock aimed at the wrong
    place."""
    import httpx
    attempts = []

    async def blocked(self, request):
        attempts.append(str(request.url))
        raise httpx.ConnectError("network disabled in tests", request=request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked)
    yield
    assert not attempts, f"test attempted real network access: {attempts}"
