# docs/content/roadmap.py
# ============================================================
# The roadmap is the highest-level structure in the docs:
# three phases, each containing a set of feature keys.
#
# Order of PHASES matters. Features are sorted by phase number
# then by feature key (see registry.all_features()).
# ============================================================

from docs.models import RoadmapPhase


PHASES = [
    RoadmapPhase(
        key='phase-1-core',
        number=1,
        title='docs.roadmap.phase-1-core.title',
        summary='docs.roadmap.phase-1-core.summary',
        feature_keys=(
            'register',
            'login',
            'first-look',
            'take-a-quiz',
            'pdfs',
            'groups',
        ),
    ),
    RoadmapPhase(
        key='phase-2-live',
        number=2,
        title='docs.roadmap.phase-2-live.title',
        summary='docs.roadmap.phase-2-live.summary',
        feature_keys=(
            'live-quiz-join',
            'live-quiz-host',
            'focus',
            'history',
            'leaderboard',
        ),
    ),
    RoadmapPhase(
        key='phase-3-premium',
        number=3,
        title='docs.roadmap.phase-3-premium.title',
        summary='docs.roadmap.phase-3-premium.summary',
        feature_keys=(
            'profile',
            'settings',
            'notifications',
            'achievements',
            'upgrade',
        ),
    ),
]