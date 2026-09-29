import pytest

from collectors import rate_limit


@pytest.fixture(autouse=True)
def _unpaced_search_limiter(monkeypatch):
    """Tests mock the network, so the process-wide search limiter's real-time
    spacing would only make the suite slow. Its own behaviour is tested on
    separate instances in test_rate_limit.py."""
    monkeypatch.setattr(rate_limit.search_limiter, "_interval", 0)
    monkeypatch.setattr(rate_limit.search_limiter, "_times", [])
