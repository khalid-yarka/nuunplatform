# services/settings_content.py
# ------------------------------------------------------------------
# User-facing copy for the "Your tier" grid on the settings page.
#
# The seed (entitlements_seed.json) uses admin-facing language
# ("Basic Statistics", "History Retention"). This file provides the
# same features with copy written for students.
#
# Keys must match feature_key values in the seed. Any feature
# present in the seed but absent here falls back to the seed's
# display_name + description — nothing disappears from the grid.
#
# Category grouping:
#   CATEGORY_ORDER  — display order of category sections
#   CATEGORY_LABELS — human label for each category key
# ------------------------------------------------------------------


CATEGORY_ORDER = [
    'quiz',
    'live_quiz',
    'resources',
    'dashboard',
    'achievements',
    'leaderboard',
    'history',
    'profile',
    'settings',
    'groups',
    'language',
]


CATEGORY_LABELS = {
    'quiz':         'Practice',
    'live_quiz':    'Competitions',
    'resources':    'PDFs & Resources',
    'dashboard':    'Analytics & Progress',
    'achievements': 'Achievements',
    'leaderboard':  'Leaderboard',
    'history':      'History',
    'profile':      'Profile & Appearance',
    'settings':     'Settings',
    'groups':       'Groups',
    'language':     'Language',
}


FEATURE_COPY = {
    # ── Practice ──
    'quiz_attempts': {
        'name': 'Practice sessions',
        'description': 'Start as many practice sessions as you want.',
        'icon': '📝',
    },
    'quiz_questions': {
        'name': 'Questions per practice',
        'description': 'Pick from 5 up to 30 questions per session.',
        'icon': '🔢',
    },
    'answer_review': {
        'name': 'Answer review',
        'description': 'See which answers were right and wrong.',
        'icon': '✅',
    },
    'correct_answer_explanations': {
        'name': 'Answer explanations',
        'description': 'See why an answer is correct after each question.',
        'icon': '💡',
    },
    'quiz_auto_advance': {
        'name': 'Auto-advance',
        'description': 'Move to the next question automatically after answering.',
        'icon': '⏭️',
    },
    'saved_content': {
        'name': 'Saved questions',
        'description': 'Bookmark questions to review them later.',
        'icon': '🔖',
    },

    # ── Competitions ──
    'create_live_quiz': {
        'name': 'Host competitions',
        'description': 'Create real-time competitions for other students.',
        'icon': '🎯',
    },
    'private_live_quiz': {
        'name': 'Private competitions',
        'description': 'Limit a competition to a join code.',
        'icon': '🔒',
    },
    'scheduled_live_quiz': {
        'name': 'Scheduled competitions',
        'description': 'Schedule a competition to start at a future time.',
        'icon': '⏰',
    },
    'live_quiz_analytics': {
        'name': 'Competition analytics',
        'description': 'See who answered what during your competition.',
        'icon': '📊',
    },

    # ── PDFs & Resources ──
    'resource_search': {
        'name': 'PDF search',
        'description': 'Search PDFs by subject, class, and curriculum.',
        'icon': '🔍',
    },
    'resource_downloads': {
        'name': 'PDF downloads',
        'description': 'Download PDFs for offline study.',
        'icon': '📥',
    },
    'premium_resources': {
        'name': 'Premium PDF library',
        'description': 'Access PDFs marked as premium.',
        'icon': '💎',
    },
    'pdf_telegram_preview': {
        'name': 'Telegram PDF preview',
        'description': 'Preview PDFs streamed from Telegram.',
        'icon': '👁️',
    },
    'pdf_direct_view': {
        'name': 'Read PDFs in the browser',
        'description': 'Open PDFs in the app without leaving the platform.',
        'icon': '📄',
    },
    'pdf_direct_download': {
        'name': 'Direct PDF download',
        'description': 'Download PDFs directly, without going through Telegram.',
        'icon': '⬇️',
    },

    # ── Analytics & Progress ──
    'basic_statistics': {
        'name': 'Basic statistics',
        'description': 'Overview of your practice performance.',
        'icon': '📈',
    },
    'performance_charts': {
        'name': 'Performance charts',
        'description': 'See your score over time as a visual chart.',
        'icon': '📉',
    },
    'subject_analytics': {
        'name': 'Subject analytics',
        'description': 'Deep dive into your per-subject performance.',
        'icon': '📚',
    },
    'progress_analytics': {
        'name': 'Progress analytics',
        'description': 'Track your improvement over time.',
        'icon': '🚀',
    },
    'quiz_analytics': {
        'name': 'Practice analytics',
        'description': 'Detailed analytics for each practice session.',
        'icon': '🔬',
    },
    'personal_learning_insights': {
        'name': 'Personal insights',
        'description': 'Suggestions for what to study next.',
        'icon': '🧠',
    },
    'focus_suggestions': {
        'name': 'Focus suggestions',
        'description': 'Study sources suggested from your wrong answers.',
        'icon': '🎯',
    },
    'focus_analytics': {
        'name': 'Focus analytics',
        'description': 'Depth of charts and analysis on the Focus page.',
        'icon': '📊',
    },
    'focus_bookmarks': {
        'name': 'Focus bookmarks',
        'description': 'Saved and liked questions on the Focus page.',
        'icon': '🔖',
    },

    # ── Achievements ──
    'achievements': {
        'name': 'Achievements',
        'description': 'Unlock achievements as you learn.',
        'icon': '🏆',
    },
    'badge_showcase': {
        'name': 'Badge showcase',
        'description': 'Display your earned badges on your profile.',
        'icon': '🎖️',
    },
    'achievement_history': {
        'name': 'Achievement history',
        'description': 'A timeline of every achievement you have unlocked.',
        'icon': '📜',
    },

    # ── Leaderboard ──
    'detailed_ranking_stats': {
        'name': 'Detailed ranking',
        'description': 'See your percentile, trend, and peers.',
        'icon': '🥇',
    },

    # ── History ──
    'history_search': {
        'name': 'History search',
        'description': 'Search inside your activity history.',
        'icon': '🔍',
    },
    'history_export': {
        'name': 'History export',
        'description': 'Download your history as a CSV file.',
        'icon': '📤',
    },
    'history_trends': {
        'name': 'History trends',
        'description': 'Visualise your trends over time.',
        'icon': '📈',
    },
    'history_delete': {
        'name': 'Delete history',
        'description': 'Delete individual history entries.',
        'icon': '🗑️',
    },
    'history_retention': {
        'name': 'History retention',
        'description': 'How long your history is kept.',
        'icon': '📅',
    },
    'history_entries': {
        'name': 'History entries',
        'description': 'Maximum number of history entries stored.',
        'icon': '🗂️',
    },

    # ── Profile & Appearance ──
    'profile_customization': {
        'name': 'Profile customization',
        'description': 'Customize your public profile.',
        'icon': '👤',
    },
    'public_id_management': {
        'name': 'Public ID management',
        'description': 'Regenerate or edit your public ID.',
        'icon': '🆔',
    },
    'appearance_customization': {
        'name': 'Appearance customization',
        'description': 'Change accent colour, font size, and font family.',
        'icon': '🎨',
    },

    # ── Settings ──
    'notification_settings': {
        'name': 'Notification settings',
        'description': 'Choose which notifications you receive.',
        'icon': '🔔',
    },

    # ── Groups ──
    'group_join': {
        'name': 'Join study groups',
        'description': 'Join WhatsApp and Telegram study groups.',
        'icon': '👥',
    },

    # ── Language ──
    'language_somali': {
        'name': 'Somali language',
        'description': 'Switch the interface to Somali.',
        'icon': '🇸🇴',
    },
}


def get_feature_copy(feature_key: str) -> dict:
    """Return the curated copy for a feature, or an empty dict."""
    return FEATURE_COPY.get(feature_key, {})


def get_category_label(category: str) -> str:
    """Return the human label for a category key."""
    return CATEGORY_LABELS.get(category, category.replace('_', ' ').title())


def get_category_order(category: str) -> int:
    """Return the display order index for a category (999 if unknown)."""
    try:
        return CATEGORY_ORDER.index(category)
    except ValueError:
        return 999