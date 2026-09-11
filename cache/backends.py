# cache/backends.py
"""
Cache backends: Redis (distributed) and LocalMemory (per‑process).
"""

import time
import logging
import threading
from typing import Optional, Any, Dict, List
from collections import OrderedDict

try:
    import redis
except ImportError:
    redis = None

logger = logging.getLogger(__name__)


class CacheBackend:
    """Base class for cache backends."""
    def get(self, key: str) -> Optional[bytes]:
        raise NotImplementedError

    def set(self, key: str, value: bytes, ttl: Optional[int] = None) -> bool:
        raise NotImplementedError

    def delete(self, key: str) -> bool:
        raise NotImplementedError

    def exists(self, key: str) -> bool:
        raise NotImplementedError

    def invalidate_pattern(self, pattern: str) -> int:
        raise NotImplementedError

    def expire(self, key: str, ttl: int) -> bool:
        raise NotImplementedError


class RedisBackend(CacheBackend):
    """Redis distributed cache backend."""
    def __init__(self, url: str, max_connections: int = 10, socket_timeout: float = 2.0):
        if redis is None:
            raise ImportError("redis library is required. Install with: pip install redis")
        self._pool = redis.ConnectionPool.from_url(
            url,
            max_connections=max_connections,
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_timeout,
            decode_responses=False,  # store bytes
        )
        self._client = redis.Redis(connection_pool=self._pool)
        self._healthy = True
        self._last_check = 0
        self._check_interval = 30  # seconds

    def _check_health(self) -> bool:
        """Ping Redis to check health, with backoff."""
        now = time.time()
        if now - self._last_check > self._check_interval:
            try:
                self._client.ping()
                self._healthy = True
            except Exception as e:
                logger.warning(f"Redis health check failed: {e}")
                self._healthy = False
            self._last_check = now
        return self._healthy

    def _ensure_healthy(self) -> bool:
        if not self._check_health():
            logger.warning("Redis is unhealthy, operations will fail.")
            return False
        return True

    def get(self, key: str) -> Optional[bytes]:
        if not self._ensure_healthy():
            return None
        try:
            return self._client.get(key)
        except Exception as e:
            logger.error(f"Redis GET error for key {key}: {e}")
            return None

    def set(self, key: str, value: bytes, ttl: Optional[int] = None) -> bool:
        if not self._ensure_healthy():
            return False
        try:
            if ttl is not None and ttl > 0:
                self._client.setex(key, ttl, value)
            else:
                self._client.set(key, value)
            return True
        except Exception as e:
            logger.error(f"Redis SET error for key {key}: {e}")
            return False

    def delete(self, key: str) -> bool:
        if not self._ensure_healthy():
            return False
        try:
            return bool(self._client.delete(key))
        except Exception as e:
            logger.error(f"Redis DELETE error for key {key}: {e}")
            return False

    def exists(self, key: str) -> bool:
        if not self._ensure_healthy():
            return False
        try:
            return bool(self._client.exists(key))
        except Exception as e:
            logger.error(f"Redis EXISTS error for key {key}: {e}")
            return False

    def invalidate_pattern(self, pattern: str) -> int:
        if not self._ensure_healthy():
            return 0
        try:
            keys = self._client.keys(pattern)
            if keys:
                return self._client.delete(*keys)
            return 0
        except Exception as e:
            logger.error(f"Redis INVALIDATE_PATTERN error for {pattern}: {e}")
            return 0

    def expire(self, key: str, ttl: int) -> bool:
        if not self._ensure_healthy():
            return False
        try:
            return self._client.expire(key, ttl)
        except Exception as e:
            logger.error(f"Redis EXPIRE error for key {key}: {e}")
            return False

    def get_metrics(self) -> Dict:
        try:
            info = self._client.info()
            return {
                'redis_version': info.get('redis_version'),
                'used_memory_human': info.get('used_memory_human'),
                'connected_clients': info.get('connected_clients'),
                'total_commands_processed': info.get('total_commands_processed'),
            }
        except Exception:
            return {'error': 'Unable to fetch metrics'}


class LocalMemoryBackend(CacheBackend):
    """
    In-memory LRU cache for ultra-fast reads.
    Uses a thread-safe OrderedDict with max size and TTL.
    """
    def __init__(self, max_size: int = 1000, default_ttl: int = 60):
        self._cache: OrderedDict[str, tuple[bytes, float]] = OrderedDict()
        self._lock = threading.Lock()
        self._max_size = max_size
        self._default_ttl = default_ttl  # seconds

    def _cleanup(self):
        """Remove expired entries."""
        now = time.time()
        with self._lock:
            for key, (value, expiry) in list(self._cache.items()):
                if expiry < now:
                    del self._cache[key]

    def get(self, key: str) -> Optional[bytes]:
        self._cleanup()
        with self._lock:
            if key in self._cache:
                value, expiry = self._cache[key]
                # Move to end (LRU)
                self._cache.move_to_end(key)
                return value
        return None

    def set(self, key: str, value: bytes, ttl: Optional[int] = None) -> bool:
        if ttl is None:
            ttl = self._default_ttl
        expiry = time.time() + ttl
        with self._lock:
            if key in self._cache:
                del self._cache[key]
            elif len(self._cache) >= self._max_size:
                # Evict oldest (first item)
                self._cache.popitem(last=False)
            self._cache[key] = (value, expiry)
            return True

    def delete(self, key: str) -> bool:
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
        return False

    def exists(self, key: str) -> bool:
        self._cleanup()
        with self._lock:
            return key in self._cache

    def invalidate_pattern(self, pattern: str) -> int:
        # Not implemented for local; no pattern matching
        return 0

    def expire(self, key: str, ttl: int) -> bool:
        # Update expiry
        with self._lock:
            if key in self._cache:
                value, _ = self._cache[key]
                expiry = time.time() + ttl
                self._cache[key] = (value, expiry)
                return True
        return False

    def get_metrics(self) -> Dict:
        with self._lock:
            return {
                'size': len(self._cache),
                'max_size': self._max_size,
                'used_percent': (len(self._cache) / self._max_size) * 100 if self._max_size else 0,
            }