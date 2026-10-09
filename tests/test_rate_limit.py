"""Unit tests for the shared search throttle."""

import asyncio

import pytest

from collectors.rate_limit import _MinuteLimiter, search_get


class _Resp:
    def __init__(self, status, headers=None, payload=None):
        self.status_code = status
        self.headers = headers or {}
        self._payload = payload or {}

    def json(self):
        return self._payload


class _Client:
    """Returns each queued response in turn and records the call count."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    async def get(self, url, headers=None):
        self.calls += 1
        return self._responses.pop(0) if self._responses else _Resp(200)


class TestMinuteLimiter:
    def _clock(self, monkeypatch):
        """Fake monotonic clock that only advances when the limiter sleeps."""
        now = {"t": 1000.0}
        sleeps = []

        async def fake_sleep(d):
            sleeps.append(d)
            now["t"] += d
        monkeypatch.setattr("collectors.rate_limit.time.monotonic", lambda: now["t"])
        monkeypatch.setattr("collectors.rate_limit.asyncio.sleep", fake_sleep)
        return now, sleeps

    def test_acquisitions_are_spaced_not_burst(self, monkeypatch):
        # A minute's allowance released at once trips GitHub's secondary limit.
        now, sleeps = self._clock(monkeypatch)
        limiter = _MinuteLimiter(3)          # 20 s apart

        async def run():
            for _ in range(3):
                await limiter.acquire()
        asyncio.run(run())
        assert [round(b - a) for a, b in zip(limiter._times, limiter._times[1:])] == [20, 20]

    def test_no_wait_once_the_interval_has_passed(self, monkeypatch):
        now, sleeps = self._clock(monkeypatch)
        limiter = _MinuteLimiter(3)

        async def run():
            await limiter.acquire()
            now["t"] += 25
            await limiter.acquire()
        asyncio.run(run())
        assert sleeps == []

    def test_old_acquisitions_expire(self, monkeypatch):
        now, sleeps = self._clock(monkeypatch)
        limiter = _MinuteLimiter(1)

        async def run():
            await limiter.acquire()
            now["t"] += 61
            await limiter.acquire()
        asyncio.run(run())
        assert sleeps == []


class TestSearchGet:
    def test_returns_the_successful_response(self, monkeypatch):
        client = _Client([_Resp(200, payload={"total_count": 7})])
        r = asyncio.run(search_get(client, "http://x", {}))
        assert r.json()["total_count"] == 7
        assert client.calls == 1

    def test_retries_on_429(self, monkeypatch):
        monkeypatch.setattr("collectors.rate_limit.asyncio.sleep", _noop)
        client = _Client([_Resp(429), _Resp(200, payload={"total_count": 1})])
        r = asyncio.run(search_get(client, "http://x", {}))
        assert r is not None
        assert client.calls == 2

    def test_retries_on_403_secondary_limit(self, monkeypatch):
        # 403 is a secondary rate limit, not a permanent denial.
        monkeypatch.setattr("collectors.rate_limit.asyncio.sleep", _noop)
        client = _Client([_Resp(403), _Resp(200, payload={"total_count": 2})])
        assert asyncio.run(search_get(client, "http://x", {})) is not None

    def test_gives_up_and_returns_none(self, monkeypatch):
        monkeypatch.setattr("collectors.rate_limit.asyncio.sleep", _noop)
        client = _Client([_Resp(429), _Resp(429), _Resp(429)])
        assert asyncio.run(search_get(client, "http://x", {})) is None

    def test_honours_retry_after(self, monkeypatch):
        seen = []

        async def record(delay):
            seen.append(delay)

        monkeypatch.setattr("collectors.rate_limit.asyncio.sleep", record)
        client = _Client([_Resp(429, headers={"Retry-After": "7"}), _Resp(200)])
        asyncio.run(search_get(client, "http://x", {}))
        assert 7.0 in seen

    def test_non_rate_limit_error_is_not_retried(self, monkeypatch):
        monkeypatch.setattr("collectors.rate_limit.asyncio.sleep", _noop)
        client = _Client([_Resp(404), _Resp(200)])
        assert asyncio.run(search_get(client, "http://x", {})) is None
        assert client.calls == 1


async def _noop(_delay):
    return None
