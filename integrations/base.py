"""Base API client with rate limiting and caching"""

import asyncio
from typing import Optional, Dict, Any
import httpx
from datetime import datetime, timedelta
import logging


class BaseAPIClient:
    def __init__(self, api_key: Optional[str] = None, rate_limit: int = 100):
        self.api_key = api_key
        self.rate_limit = rate_limit
        self.requests_made = 0
        self.window_start = datetime.now()
        self.logger = logging.getLogger(self.__class__.__name__)

    async def _check_rate_limit(self):
        """Ensure we don't exceed rate limits"""
        now = datetime.now()
        window_duration = timedelta(hours=1)

        if now - self.window_start > window_duration:
            self.requests_made = 0
            self.window_start = now

        if self.requests_made >= self.rate_limit:
            sleep_time = (window_duration - (now - self.window_start)).total_seconds()
            self.logger.warning(f"Rate limit reached, sleeping {sleep_time}s")
            await asyncio.sleep(sleep_time)
            self.requests_made = 0
            self.window_start = datetime.now()

        self.requests_made += 1
