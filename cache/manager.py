# cache/manager.py
"""
Main CacheManager class with multi-tier caching, stampede protection,
fallback, and metrics.
"""

import time
import logging
import threading
from typing import Optional, Any, Callable, Dict, List, Union

from .backends import RedisBackend, LocalMemoryBackend, CacheBackend
from .serializers import serialize, deserialize
from .keys import make_key, pattern_for_entity, pattern_for_namespace
from .metrics import cache_metrics

logger = logging.getLogger(__name__)


class CacheManager:
    """
    Unified cache manager with two tiers:
        - L1: LocalMemoryBackend (per‑process, ultra‑fast)
        - L2: RedisBackend (distributed, persistent)

    If Redis is unavailable or not configured, the manager falls back to L1 only.
    """

    def __init__(
        self,
        redis_url: Optional[str] = None,
        local_max_size: int = 1000,
        local_ttl: int = 60,
        default_serialization: str = 'json',
        redis_max_connections: int = 10,
    ):
        self._local = LocalMemoryBackend(max_size=local_max_size, default_ttl=local_ttl)
        self._redis = None
        self._redis_available = False
        if redis_url and redis_url.strip():
            try:
                self._redis = RedisBackend(redis_url, max_connections=redis_max_connections)
                self._redis_available = True
                logger.info("Redis backend initialized successfully.")
            except Exception as e:
                logger.error(f"Failed to initialize Redis backend: {e}")
                self._redis = None
                self._redis_available = False
        else:
            logger.info("Redis URL not provided; using local cache only.")

        self._default_serialization = default_serialization
        self._lock_ttl = 5  # seconds for stampede prevention lock
        self._lock_retries = 5
        self._lock_retry_delay = 0.1  # seconds

    @property
    def redis_available(self) -> bool:
        return self._redis_available and self._redis is not None

    def _get_redis(self) -> Optional[RedisBackend]:
        if not self.redis_available:
            return None
        # Health check
        if not self._redis._check_health():
            logger.warning("Redis is unhealthy, falling back to local cache.")
            self._redis_available = False
            return None
        return self._redis

    def get(
        self,
        key: str,
        namespace: Optional[str] = None,
        fetch_func: Optional[Callable] = None,
        ttl: Optional[int] = None,
        serialization: Optional[str] = None,
    ) -> Optional[Any]:
        """
        Get a value from cache. If missing and fetch_func is provided, call it
        to load from source and store with ttl.

        Returns deserialized value or None.
        """
        full_key = key if namespace is None else make_key(namespace, '', key) if ':' not in key else key

        # Try L1
        value_bytes = self._local.get(full_key)
        if value_bytes is not None:
            cache_metrics.record_hit('local')
            return deserialize(value_bytes, serialization or self._default_serialization)

        # Try L2 (Redis)
        redis = self._get_redis()
        if redis:
            value_bytes = redis.get(full_key)
            if value_bytes is not None:
                # Populate L1
                self._local.set(full_key, value_bytes, ttl=ttl or 60)
                cache_metrics.record_hit('redis')
                return deserialize(value_bytes, serialization or self._default_serialization)

        # Cache miss
        cache_metrics.record_miss()

        if fetch_func is not None:
            # Stampede prevention: acquire lock
            lock_key = f"lock:{full_key}"
            acquired = False
            if redis:
                # Try to acquire lock via Redis
                for attempt in range(self._lock_retries):
                    if redis.set(lock_key, b'1', ttl=self._lock_ttl, nx=True):
                        acquired = True
                        break
                    time.sleep(self._lock_retry_delay * (2 ** attempt))
            # If Redis unavailable or lock not acquired, we still compute (fallback)
            if not acquired:
                logger.debug(f"Could not acquire lock for {full_key}, computing directly.")

            try:
                # Fetch from source
                result = fetch_func()
                if result is not None:
                    self.set(full_key, result, ttl=ttl, serialization=serialization)
                return result
            finally:
                if acquired and redis:
                    redis.delete(lock_key)

        return None

    def set(
        self,
        key: str,
        value: Any,
        ttl: Optional[int] = None,
        namespace: Optional[str] = None,
        serialization: Optional[str] = None,
    ) -> bool:
        """
        Store a value in both L1 and L2.
        """
        full_key = key if namespace is None else make_key(namespace, '', key) if ':' not in key else key
        serial = serialization or self._default_serialization
        data_bytes = serialize(value, serial)

        success = True
        # L1
        if not self._local.set(full_key, data_bytes, ttl=ttl or 60):
            success = False

        # L2
        redis = self._get_redis()
        if redis:
            if not redis.set(full_key, data_bytes, ttl=ttl):
                success = False
        return success

    def delete(self, key: str, namespace: Optional[str] = None) -> bool:
        """Delete a key from both tiers."""
        full_key = key if namespace is None else make_key(namespace, '', key) if ':' not in key else key
        success = True
        if not self._local.delete(full_key):
            success = False
        redis = self._get_redis()
        if redis:
            if not redis.delete(full_key):
                success = False
        return success

    def exists(self, key: str, namespace: Optional[str] = None) -> bool:
        full_key = key if namespace is None else make_key(namespace, '', key) if ':' not in key else key
        if self._local.exists(full_key):
            return True
        redis = self._get_redis()
        if redis:
            return redis.exists(full_key)
        return False

    def expire(self, key: str, ttl: int, namespace: Optional[str] = None) -> bool:
        """Update TTL for a key in both tiers."""
        full_key = key if namespace is None else make_key(namespace, '', key) if ':' not in key else key
        success = True
        if not self._local.expire(full_key, ttl):
            success = False
        redis = self._get_redis()
        if redis:
            if not redis.expire(full_key, ttl):
                success = False
        return success

    def invalidate_pattern(self, pattern: str) -> int:
        """
        Invalidate all keys matching a pattern.
        Supports wildcard '*' in pattern.
        Works only on Redis; local cache cannot pattern-delete efficiently.
        """
        redis = self._get_redis()
        if redis:
            count = redis.invalidate_pattern(pattern)
            # Clear local cache for matched keys? Can't pattern-delete easily.
            # We'll clear all local cache to be safe (aggressive, but ensures consistency).
            if count > 0:
                self._local._cache.clear()
            return count
        return 0

    def invalidate_namespace(self, namespace: str) -> int:
        """Invalidate all keys in a namespace."""
        return self.invalidate_pattern(pattern_for_namespace(namespace))

    def invalidate_entity(self, namespace: str, entity: str) -> int:
        """Invalidate all keys for a specific entity within a namespace."""
        return self.invalidate_pattern(pattern_for_entity(namespace, entity))

    def get_or_compute(
        self,
        key: str,
        compute_func: Callable,
        ttl: Optional[int] = None,
        namespace: Optional[str] = None,
        serialization: Optional[str] = None,
    ) -> Any:
        """Alias for get with fetch_func."""
        return self.get(
            key=key,
            namespace=namespace,
            fetch_func=compute_func,
            ttl=ttl,
            serialization=serialization,
        )

    def get_metrics(self) -> Dict:
        """Get cache metrics."""
        return {
            'local': self._local.get_metrics(),
            'redis': self._redis.get_metrics() if self._redis else {'available': False},
            'global': {
                'hits': cache_metrics.hits,
                'misses': cache_metrics.misses,
                'hit_ratio': cache_metrics.hit_ratio,
            }
        }


# Singleton instance
_cache_manager = None
_cache_manager_lock = threading.Lock()


def get_cache_manager() -> CacheManager:
    """Get the global CacheManager instance (singleton)."""
    global _cache_manager
    if _cache_manager is None:
        with _cache_manager_lock:
            if _cache_manager is None:
                from config import Config
                _cache_manager = CacheManager(
                    redis_url=getattr(Config, 'REDIS_URL', ''),   # Safe fallback
                    local_max_size=getattr(Config, 'CACHE_LOCAL_MAX_SIZE', 1000),
                    local_ttl=getattr(Config, 'CACHE_LOCAL_TTL', 60),
                    default_serialization=getattr(Config, 'CACHE_SERIALIZATION', 'json'),
                    redis_max_connections=getattr(Config, 'REDIS_MAX_CONNECTIONS', 10),
                )
    return _cache_manager