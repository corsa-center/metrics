"""
Shared rate limiting for the GitHub APIs.

Two distinct limits matter, and neither is the 5,000/hour core budget that
usually gets attention:

  - The **search** API allows 30 requests per minute, authenticated. The
    outreach and reliability collectors are search-based, so a 67-package run
    issues several hundred search calls and will sit on that ceiling.
  - GitHub's **secondary** limits react to concurrency rather than volume, and
    answer 403 (not 429) when several requests for the same repository land at
    once.

A failed check is indistinguishable from a negative result — a rate-limited
file probe looks exactly like "file absent" — so throttling is a correctness
concern, not just a politeness one.
"""

import asyncio
import logging
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Search allows 30/min; leaving headroom absorbs clock skew and retries.
_SEARCH_PER_MINUTE = 20


class _MinuteLimiter:
    """Allows at most `limit` acquisitions in any rolling 60-second window."""

    def __init__(self, limit: int):
        self._limit = limit
        self._times: list = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._times = [t for t in self._times if now - t < 60]
                if len(self._times) < self._limit:
                    self._times.append(now)
                    return
                wait = 60 - (now - self._times[0]) + 0.1
                logger.debug(f"Search rate limit reached, waiting {wait:.1f}s")
                await asyncio.sleep(wait)


# One limiter per process, shared by every collector that searches.
search_limiter = _MinuteLimiter(_SEARCH_PER_MINUTE)


async def search_get(
    client: httpx.AsyncClient, url: str, headers: dict, attempts: int = 3
) -> Optional[httpx.Response]:
    """GET a search URL under the shared throttle, retrying on rate limits.

    Honours Retry-After when GitHub sends it, and treats 403 as a secondary
    rate limit rather than a permanent failure — that is how GitHub signals
    "too fast", and giving up on it silently produces a wrong answer.
    """
    for attempt in range(attempts):
        await search_limiter.acquire()
        try:
            resp = await client.get(url, headers=headers)
        except Exception as e:
            logger.debug(f"Search request error: {e}")
            if attempt == attempts - 1:
                return None
            await asyncio.sleep(2 ** attempt)
            continue

        if resp.status_code == 200:
            return resp
        if resp.status_code in (403, 429) and attempt < attempts - 1:
            retry_after = resp.headers.get("Retry-After")
            delay = float(retry_after) if retry_after else min(60, 5 * (2 ** attempt))
            logger.debug(f"Search {resp.status_code}, backing off {delay:.0f}s")
            await asyncio.sleep(delay)
            continue
        return resp if resp.status_code == 200 else None
    return None
