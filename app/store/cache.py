"""缓存后端。MVP 用进程内 LRU+TTL；多实例时换 Redis，接口不变。

刻意不引入 cachetools：一个 40 行的 LRU 足够，少一个依赖少一个坑。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Protocol


class CacheBackend(Protocol):
    def get(self, key: str) -> Any | None: ...
    def set(self, key: str, value: Any, ttl_s: int = 3600) -> None: ...
    def clear(self) -> None: ...


class MemoryCache:
    def __init__(self, max_items: int = 200) -> None:
        self._max = max_items
        self._data: dict[str, tuple[Any, float]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            value, expire_at = item
            if expire_at < time.time():
                self._data.pop(key, None)
                return None
            # 触碰即置为新，天然 LRU
            self._data.pop(key)
            self._data[key] = (value, expire_at)
            return value

    def set(self, key: str, value: Any, ttl_s: int = 3600) -> None:
        with self._lock:
            if key in self._data:
                self._data.pop(key)
            elif len(self._data) >= self._max:
                self._data.pop(next(iter(self._data)))
            self._data[key] = (value, time.time() + ttl_s)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


_backend: CacheBackend | None = None


def get_cache() -> CacheBackend:
    global _backend
    if _backend is None:
        from app.config import get_settings

        _backend = MemoryCache(get_settings().get("cache.memory_max_items", 200))
    return _backend
