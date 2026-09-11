# services/notification_service.py
"""
Notification service that respects user preferences.
"""

import logging
from typing import Optional
from db import create_notification, create_notification_for_all_users as db_create_all
from services.settings_service import SettingsService

logger = logging.getLogger(__name__)


def send_notification(
    user_id: int,
    notification_type: str,
    title: str,
    body: str,
    link: str = '',
    icon: str = '',
    force: bool = False
) -> bool:
    """
    Send a notification to a single user if they have enabled it (or if forced).
    Returns True if notification was sent, False if skipped due to preference.
    """
    if not force:
        if not SettingsService.get_notification_preference(user_id, notification_type):
            logger.debug(f"Notification '{notification_type}' disabled for user {user_id}")
            return False

    return create_notification(user_id, notification_type, title, body, link, icon)


def send_notification_to_all(
    notification_type: str,
    title: str,
    body: str,
    link: str = '',
    icon: str = '',
    force: bool = False
) -> int:
    """
    Send a notification to all users, respecting each user's preference unless forced.
    Returns number of notifications actually sent.
    """
    if force:
        return db_create_all(notification_type, title, body, link, icon)

    from db import execute_with_retry
    cursor = execute_with_retry("SELECT id FROM students")
    users = cursor.fetchall()
    sent = 0
    for row in users:
        user_id = row['id']
        if SettingsService.get_notification_preference(user_id, notification_type):
            create_notification(user_id, notification_type, title, body, link, icon)
            sent += 1
    return sent