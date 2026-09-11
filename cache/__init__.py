# cache/__init__.py
"""
Cache system for NuunPlatform.
Provides a unified interface for caching with Redis and in-memory fallback.
"""

from .manager import CacheManager, get_cache_manager
from .backends import RedisBackend, LocalMemoryBackend
from .keys import make_key, parse_key, get_namespace_from_key
from .serializers import serialize, deserialize
from .invalidators import InvalidationHelper
from .metrics import cache_metrics
from .worker import start_worker, stop_worker

__all__ = [
    'CacheManager',
    'get_cache_manager',
    'RedisBackend',
    'LocalMemoryBackend',
    'make_key',
    'parse_key',
    'get_namespace_from_key',
    'serialize',
    'deserialize',
    'InvalidationHelper',
    'cache_metrics',
    'start_worker',
    'stop_worker',
]