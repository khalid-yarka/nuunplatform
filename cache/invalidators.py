# cache/invalidators.py
"""
Helper functions to trigger invalidation based on events.
"""

import logging
from typing import List, Optional

from .manager import get_cache_manager
from .keys import pattern_for_entity, pattern_for_namespace, make_key

logger = logging.getLogger(__name__)


class InvalidationHelper:
    """Static methods to invalidate cache for specific data changes."""

    @staticmethod
    def invalidate_user(user_id: int):
        """Invalidate all cache entries for a specific user."""
        cache = get_cache_manager()
        cache.invalidate_pattern(f"user:profile:{user_id}:*")
        cache.invalidate_pattern(f"user:preferences:{user_id}:*")
        # Also invalidate any leaderboard that might include this user
        cache.invalidate_pattern("leaderboard:*")

    @staticmethod
    def invalidate_subject(subject_id: Optional[int] = None):
        """Invalidate subject lists and specific subject if provided."""
        cache = get_cache_manager()
        if subject_id:
            cache.invalidate_pattern(f"subject:data:{subject_id}:*")
        cache.invalidate_pattern("subject:list:*")

    @staticmethod
    def invalidate_quiz(quiz_id: int):
        """Invalidate all quiz-related cache for a specific quiz."""
        cache = get_cache_manager()
        cache.invalidate_pattern(f"quiz:state:{quiz_id}:*")
        cache.invalidate_pattern(f"quiz:participants:{quiz_id}:*")
        cache.invalidate_pattern(f"quiz:leaderboard:{quiz_id}:*")

    @staticmethod
    def invalidate_pdf(pdf_id: Optional[int] = None):
        """Invalidate PDF metadata."""
        cache = get_cache_manager()
        if pdf_id:
            cache.invalidate_pattern(f"pdf:metadata:{pdf_id}:*")
        cache.invalidate_pattern("pdf:list:*")

    @staticmethod
    def invalidate_group(group_id: Optional[int] = None):
        """Invalidate group cache."""
        cache = get_cache_manager()
        if group_id:
            cache.invalidate_pattern(f"group:data:{group_id}:*")
        cache.invalidate_pattern("group:list:*")

    @staticmethod
    def invalidate_admin_stats():
        """Invalidate admin dashboard statistics."""
        cache = get_cache_manager()
        cache.invalidate_pattern("admin:stats:*")

    @staticmethod
    def invalidate_notification(user_id: int):
        """Invalidate notification cache for a user."""
        cache = get_cache_manager()
        cache.invalidate_pattern(f"notification:list:{user_id}:*")
        cache.invalidate_pattern(f"notification:unread:{user_id}:*")

    @staticmethod
    def invalidate_leaderboard(subject_id: Optional[int] = None):
        """Invalidate leaderboard cache."""
        cache = get_cache_manager()
        if subject_id:
            cache.invalidate_pattern(f"leaderboard:subject:{subject_id}:*")
        cache.invalidate_pattern("leaderboard:global:*")

    @staticmethod
    def invalidate_session(session_id: str):
        """Invalidate a specific session."""
        cache = get_cache_manager()
        cache.delete(f"session:{session_id}")