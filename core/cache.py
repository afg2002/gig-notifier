"""
Cache classes — in-memory caches for scraped data to avoid re-scraping.
"""
import time
import logging

logger = logging.getLogger(__name__)

TTL = 300  # 5 minutes


class ProjectCache:
    """Cache for projects.co.id listings."""

    def __init__(self):
        self._data: dict = {}
        self._timestamp: float = 0

    def get(self, key: str):
        if time.time() - self._timestamp < TTL:
            return self._data.get(key)
        return None

    def set(self, key: str, value):
        self._data[key] = value
        self._timestamp = time.time()

    def is_fresh(self) -> bool:
        return time.time() - self._timestamp < TTL


class FastworkJobCache:
    """Cache for Fastwork.id job listings."""

    def __init__(self):
        self._data: dict = {}
        self._timestamp: float = 0

    def get(self, key: str):
        if time.time() - self._timestamp < TTL:
            return self._data.get(key)
        return None

    def set(self, key: str, value):
        self._data[key] = value
        self._timestamp = time.time()

    def is_fresh(self) -> bool:
        return time.time() - self._timestamp < TTL


class SribuContestCache:
    """Cache for Sribu.com contest listings."""

    def __init__(self):
        self._data: dict = {}
        self._timestamp: float = 0

    def get(self, key: str):
        if time.time() - self._timestamp < TTL:
            return self._data.get(key)
        return None

    def set(self, key: str, value):
        self._data[key] = value
        self._timestamp = time.time()

    def is_fresh(self) -> bool:
        return time.time() - self._timestamp < TTL
