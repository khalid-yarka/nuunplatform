# docs/content/account.py
# ============================================================
# Five account-related features, all in phase 3.
# ============================================================

from docs.models import Feature, Step


# ------------------------------------------------------------
# PROFILE
# ------------------------------------------------------------

profile = Feature(
    key='profile',
    title='docs.profile.title',
    tagline='docs.profile.tagline',
    icon='👤',
    phase='phase-3-premium',
    tier='free',
    steps=(
        Step(
            title='docs.profile.step1.title',
            action='docs.profile.step1.action',
            mockup='profile_page',
            hotspots=('profile_card', 'avatar', 'public_id'),
            try_url='/profile/',
        ),
        Step(
            title='docs.profile.step2.title',
            action='docs.profile.step2.action',
            mockup='profile_page',
            hotspots=('details', 'total_points'),
            tip='docs.profile.step2.tip',
            try_url='/profile/',
        ),
    ),
    prerequisites=(),
    related=('settings', 'achievements'),
)


# ------------------------------------------------------------
# SETTINGS
# ------------------------------------------------------------

settings = Feature(
    key='settings',
    title='docs.settings.title',
    tagline='docs.settings.tagline',
    icon='⚙️',
    phase='phase-3-premium',
    tier='free',
    steps=(
        Step(
            title='docs.settings.step1.title',
            action='docs.settings.step1.action',
            mockup='settings_page',
            hotspots=('settings_grid',),
            try_url='/settings/',
        ),
        Step(
            title='docs.settings.step2.title',
            action='docs.settings.step2.action',
            mockup='settings_page',
            hotspots=('appearance_card',),
            tip='docs.settings.step2.tip',
            try_url='/settings/#appearance',
        ),
        Step(
            title='docs.settings.step3.title',
            action='docs.settings.step3.action',
            mockup='settings_page',
            hotspots=('quiz_card', 'notifications_card'),
            try_url='/settings/#quiz',
        ),
        Step(
            title='docs.settings.step4.title',
            action='docs.settings.step4.action',
            mockup='settings_page',
            hotspots=('privacy_card', 'tier_card'),
            try_url='/settings/#privacy',
        ),
    ),
    prerequisites=(),
    related=('profile', 'upgrade'),
)


# ------------------------------------------------------------
# NOTIFICATIONS
# ------------------------------------------------------------

notifications = Feature(
    key='notifications',
    title='docs.notifications.title',
    tagline='docs.notifications.tagline',
    icon='🔔',
    phase='phase-3-premium',
    tier='free',
    steps=(
        Step(
            title='docs.notifications.step1.title',
            action='docs.notifications.step1.action',
            mockup='notifications_page',
            hotspots=('unread_count',),
            try_url='/notifications/',
        ),
        Step(
            title='docs.notifications.step2.title',
            action='docs.notifications.step2.action',
            mockup='notifications_page',
            hotspots=('notification_list', 'notification_row'),
            try_url='/notifications/',
        ),
        Step(
            title='docs.notifications.step3.title',
            action='docs.notifications.step3.action',
            mockup='notifications_page',
            hotspots=('mark_all_btn', 'mark_read_btn'),
            tip='docs.notifications.step3.tip',
            try_url='/notifications/',
        ),
    ),
    prerequisites=(),
    related=('settings',),
)


# ------------------------------------------------------------
# ACHIEVEMENTS
# ------------------------------------------------------------

achievements = Feature(
    key='achievements',
    title='docs.achievements.title',
    tagline='docs.achievements.tagline',
    icon='🏅',
    phase='phase-3-premium',
    tier='free',
    steps=(
        Step(
            title='docs.achievements.step1.title',
            action='docs.achievements.step1.action',
            mockup='achievements_page',
            hotspots=('achievements_header',),
            try_url='/achievements/',
        ),
        Step(
            title='docs.achievements.step2.title',
            action='docs.achievements.step2.action',
            mockup='achievements_page',
            hotspots=('achievements_grid', 'achievement_card'),
            try_url='/achievements/',
        ),
        Step(
            title='docs.achievements.step3.title',
            action='docs.achievements.step3.action',
            mockup='achievements_page',
            hotspots=('showcase_section',),
            tip='docs.achievements.step3.tip',
            try_url='/achievements/',
        ),
    ),
    prerequisites=('take-a-quiz',),
    related=('profile',),
)


# ------------------------------------------------------------
# UPGRADE
# ------------------------------------------------------------

upgrade = Feature(
    key='upgrade',
    title='docs.upgrade.title',
    tagline='docs.upgrade.tagline',
    icon='⭐',
    phase='phase-3-premium',
    tier='free',
    steps=(
        Step(
            title='docs.upgrade.step1.title',
            action='docs.upgrade.step1.action',
            mockup='home_page',
            hotspots=('tier_strip',),
            try_url='/upgrade/',
        ),
        Step(
            title='docs.upgrade.step2.title',
            action='docs.upgrade.step2.action',
            mockup='home_page',
            hotspots=('home_hero',),
            tip='docs.upgrade.step2.tip',
            try_url='/upgrade/',
        ),
        Step(
            title='docs.upgrade.step3.title',
            action='docs.upgrade.step3.action',
            mockup='settings_page',
            hotspots=('tier_card',),
            try_url='/settings/#tier',
        ),
        Step(
            title='docs.upgrade.step4.title',
            action='docs.upgrade.step4.action',
            mockup='home_page',
            hotspots=('tier_strip',),
            tip='docs.upgrade.step4.tip',
            try_url='/upgrade/',
        ),
    ),
    prerequisites=(),
    related=('settings',),
)


# ------------------------------------------------------------
# EXPORT
# ------------------------------------------------------------

FEATURES = [
    profile,
    settings,
    notifications,
    achievements,
    upgrade,
]