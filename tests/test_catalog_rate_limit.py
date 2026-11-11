"""Catalog fetches wait out a GitHub rate limit instead of failing the run."""

import asyncio
import time

import httpx
import pytest

from catalog import catalog as catalog_module
from catalog.catalog import Catalog, _rate_limit_wait, _wait_limit_get

URL = "https://api.github.com/repos/o/r/contents/package_config"


def _response(status, **headers):
    return httpx.Response(status, headers=headers)


class TestRateLimitWait:
    def test_retry_after(self):
        assert _rate_limit_wait(_response(403, **{"retry-after": "30"})) == 30

    def test_exhausted_quota_waits_until_reset(self):
        reset = str(int(time.time()) + 600)
        wait = _rate_limit_wait(_response(403, **{"x-ratelimit-remaining": "0", "x-ratelimit-reset": reset}))
        assert 600 <= wait <= 606

    def test_a_permission_403_is_not_a_rate_limit(self):
        assert _rate_limit_wait(_response(403)) is None
        assert _rate_limit_wait(_response(403, **{"x-ratelimit-remaining": "4000"})) is None

    def test_other_statuses(self):
        assert _rate_limit_wait(_response(200, **{"retry-after": "30"})) is None
        assert _rate_limit_wait(_response(404)) is None


class _Sequence:
    """Transport answering successive requests from a list."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    def __call__(self, request):
        self.calls += 1
        return self.responses.pop(0)


@pytest.fixture
def sleeps(monkeypatch):
    waited = []

    async def fake_sleep(seconds):
        waited.append(seconds)

    monkeypatch.setattr(catalog_module.asyncio, "sleep", fake_sleep)
    return waited


def _run_get(transport):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            return await _wait_limit_get(client, URL, {})
    return asyncio.run(go())


class TestGet:
    def test_waits_for_the_reset_then_retries(self, sleeps):
        reset = str(int(time.time()) + 300)
        transport = _Sequence(
            _response(403, **{"x-ratelimit-remaining": "0", "x-ratelimit-reset": reset}),
            httpx.Response(200, json={"entries": []}),
        )
        assert _run_get(transport).status_code == 200
        assert transport.calls == 2
        assert len(sleeps) == 1 and 300 <= sleeps[0] <= 306

    def test_does_not_wait_past_the_limit(self, sleeps):
        reset = str(int(time.time()) + catalog_module.MAX_RATE_LIMIT_WAIT + 600)
        transport = _Sequence(_response(403, **{"x-ratelimit-remaining": "0", "x-ratelimit-reset": reset}))
        assert _run_get(transport).status_code == 403
        assert sleeps == [] and transport.calls == 1

    def test_a_permission_403_is_returned_at_once(self, sleeps):
        transport = _Sequence(_response(403))
        assert _run_get(transport).status_code == 403
        assert sleeps == [] and transport.calls == 1


class TestFetchCatalogFiles:
    def test_rate_limited_directory_listing_recovers(self, sleeps, monkeypatch):
        transport = _Sequence(
            _response(403, **{"retry-after": "60"}),
            httpx.Response(200, json={"entries": [{"name": "a.yaml", "type": "file"}]}),
        )
        real_client = httpx.AsyncClient
        monkeypatch.setattr(catalog_module.httpx, "AsyncClient",
                            lambda **kw: real_client(transport=httpx.MockTransport(transport), **kw))
        files = asyncio.run(Catalog(URL)._fetch_catalog_files(URL))
        assert files == [{"name": "a.yaml", "type": "file"}]
        assert sleeps == [60]
