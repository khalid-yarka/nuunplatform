# cache/metrics.py
"""
Simple metrics collection for cache operations.
"""

import threading
from typing import Dict, Any


class CacheMetrics:
    """Thread-safe metrics collector."""

    def __init__(self):
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.hit_local = 0
        self.hit_redis = 0

    def record_hit(self, tier: str = 'local'):
        with self._lock:
            self.hits += 1
            if tier == 'local':
                self.hit_local += 1
            elif tier == 'redis':
                self.hit_redis += 1

    def record_miss(self):
        with self._lock:
            self.misses += 1

    @property
    def hit_ratio(self) -> float:
        total = self.hits + self.misses
        if total == 0:
            return 0.0
        return round(self.hits / total, 4)

    def reset(self):
        with self._lock:
            self.hits = 0
            self.misses = 0
            self.hit_local = 0
            self.hit_redis = 0

    def get_summary(self) -> Dict[str, Any]:
        with self._lock:
            return {
                'hits': self.hits,
                'misses': self.misses,
                'hit_ratio': self.hit_ratio,
                'hit_local': self.hit_local,
                'hit_redis': self.hit_redis,
            }


cache_metrics = CacheMetrics()