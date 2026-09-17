# docs/registry.py
# ============================================================
# Loads every content module once at import time, merges them
# into flat dicts, and exposes lookup helpers.
#
# Validation is best-effort: missing mockups, unknown hotspot
# anchors, and dangling feature references all log a warning
# but never crash the app.
# ============================================================

import logging
from typing import Dict, List, Optional

from docs.models import Feature, RoadmapPhase, TourEntry

logger = logging.getLogger(__name__)


# ============================================================
# TEMPLATE MAP
# ------------------------------------------------------------
# Every `Step.mockup` must be a key in this dict.
# The value is the template name relative to templates/docs/mockups/.
# ============================================================

MOCKUP_TEMPLATES: Dict[str, str] = {
    'register_page':      'docs/mockups/register.html',
    'login_page':         'docs/mockups/login.html',
    'home_page':          'docs/mockups/home.html',
    'history_page':       'docs/mockups/history.html',
    'profile_page':       'docs/mockups/profile.html',
    'quiz_setup':         'docs/mockups/quiz_setup.html',
    'quiz_play':          'docs/mockups/quiz_play.html',
    'quiz_results':       'docs/mockups/quiz_results.html',
    'live_lobby':         'docs/mockups/live_lobby.html',
    'live_join':          'docs/mockups/live_join.html',
    'live_waiting':       'docs/mockups/live_waiting.html',
    'live_play':          'docs/mockups/live_play.html',
    'pdfs_page':          'docs/mockups/pdfs.html',
    'groups_page':        'docs/mockups/groups.html',
    'focus_page':         'docs/mockups/focus.html',
    'settings_page':      'docs/mockups/settings.html',
    'notifications_page': 'docs/mockups/notifications.html',
    'achievements_page':  'docs/mockups/achievements.html',
    'leaderboard_page':   'docs/mockups/leaderboard.html',
}


# ============================================================
# LOAD & MERGE
# ============================================================

def _load_content():
    """
    Import the content package and pull out its aggregated
    collections. Returns (features, phases, tour).
    """
    try:
        from docs import content
    except Exception as e:
        logger.warning("docs.content failed to import: %s", e)
        return [], [], []

    features = list(getattr(content, 'ALL_FEATURES', []) or [])
    phases = list(getattr(content, 'PHASES', []) or [])
    tour = list(getattr(content, 'TOUR_SEQUENCE', []) or [])

    # Assign tour order (1-indexed) if authors didn't.
    tour = [
        TourEntry(order=i + 1,
                  feature_key=t.feature_key,
                  step_number=t.step_number)
        for i, t in enumerate(tour)
    ]

    return features, phases, tour


_ALL_FEATURES, _ALL_PHASES, _TOUR = _load_content()

_FEATURES_BY_KEY: Dict[str, Feature] = {f.key: f for f in _ALL_FEATURES}
_PHASES_BY_KEY:   Dict[str, RoadmapPhase] = {p.key: p for p in _ALL_PHASES}


# ============================================================
# PUBLIC LOOKUPS
# ============================================================

def all_features() -> List[Feature]:
    """Every feature, sorted by phase number then by key."""
    def sort_key(f: Feature):
        phase = _PHASES_BY_KEY.get(f.phase)
        return (phase.number if phase else 999, f.key)
    return sorted(_ALL_FEATURES, key=sort_key)


def get_feature(key: str) -> Optional[Feature]:
    return _FEATURES_BY_KEY.get(key)


def features_for_phase(phase_key: str) -> List[Feature]:
    return [f for f in all_features() if f.phase == phase_key]


def all_phases() -> List[RoadmapPhase]:
    return sorted(_ALL_PHASES, key=lambda p: p.number)


def get_phase(key: str) -> Optional[RoadmapPhase]:
    return _PHASES_BY_KEY.get(key)


def tour_sequence() -> List[TourEntry]:
    return list(_TOUR)


def feature_keys() -> List[str]:
    return [f.key for f in all_features()]


def total_step_count() -> int:
    return sum(f.step_count for f in _ALL_FEATURES)


def mockup_template(mockup_id: str) -> Optional[str]:
    """Return the template path for a mockup id, or None."""
    return MOCKUP_TEMPLATES.get(mockup_id)


# ============================================================
# VALIDATION (best-effort, runs once at import)
# ============================================================

def _validate():
    """
    Log warnings for anything that looks wrong. Never raises.
    Called once at import. Failures do not block startup.
    """
    problems: List[str] = []

    # 1. Every feature references a real phase.
    for f in _ALL_FEATURES:
        if f.phase not in _PHASES_BY_KEY:
            problems.append(f"feature {f.key!r} references unknown phase {f.phase!r}")

    # 2. Every phase references real feature keys.
    for p in _ALL_PHASES:
        for fk in p.feature_keys:
            if fk not in _FEATURES_BY_KEY:
                problems.append(f"phase {p.key!r} references unknown feature {fk!r}")

    # 3. Every step references a real mockup template.
    for f in _ALL_FEATURES:
        for s in f.steps:
            if s.mockup not in MOCKUP_TEMPLATES:
                problems.append(
                    f"feature {f.key!r} step {s.number} has unknown mockup {s.mockup!r}"
                )

    # 4. Every feature's prerequisites / related exist.
    for f in _ALL_FEATURES:
        for ref in (*f.prerequisites, *f.related):
            if ref not in _FEATURES_BY_KEY:
                problems.append(f"feature {f.key!r} references unknown feature {ref!r}")

    # 5. Tour entries resolve.
    for t in _TOUR:
        f = _FEATURES_BY_KEY.get(t.feature_key)
        if not f:
            problems.append(f"tour entry references unknown feature {t.feature_key!r}")
            continue
        if not f.get_step(t.step_number):
            problems.append(
                f"tour entry references missing step {t.step_number} "
                f"of feature {t.feature_key!r}"
            )

    if problems:
        logger.warning("docs registry: %d content issue(s) detected:", len(problems))
        for p in problems:
            logger.warning("  - %s", p)
    else:
        logger.info(
            "docs registry loaded: %d feature(s), %d phase(s), %d step(s), tour=%d",
            len(_ALL_FEATURES), len(_ALL_PHASES), total_step_count(), len(_TOUR),
        )


_validate()