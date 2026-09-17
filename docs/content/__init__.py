# docs/content/__init__.py
# ============================================================
# Aggregates every content module into three collections:
#
#   ALL_FEATURES   — flat list of Feature objects
#   PHASES         — roadmap phases (ordered)
#   TOUR_SEQUENCE  — the guided tour, as a list of TourEntry
#
# Missing or empty content modules are tolerated: they simply
# contribute nothing. This lets you build incrementally without
# the app crashing on a half-written file.
# ============================================================

import logging

logger = logging.getLogger(__name__)


def _safe_import(module_name: str):
    """Import docs.content.<module_name>; return None on failure."""
    try:
        mod = __import__(f'docs.content.{module_name}', fromlist=['*'])
        return mod
    except Exception as e:
        logger.warning("docs.content.%s failed to import: %s", module_name, e)
        return None


def _features_from(mod) -> list:
    if mod is None:
        return []
    return list(getattr(mod, 'FEATURES', []) or [])


# ---- Import the content modules ----------------------------

_roadmap_mod      = _safe_import('roadmap')
_getting_started  = _safe_import('getting_started')
_learning         = _safe_import('learning')
_account          = _safe_import('account')
_guided_tour      = _safe_import('guided_tour')


# ---- Aggregate ---------------------------------------------

ALL_FEATURES = (
    _features_from(_getting_started)
    + _features_from(_learning)
    + _features_from(_account)
)

PHASES = list(getattr(_roadmap_mod, 'PHASES', []) or []) if _roadmap_mod else []

TOUR_SEQUENCE = (
    list(getattr(_guided_tour, 'TOUR_SEQUENCE', []) or [])
    if _guided_tour else []
)


__all__ = ['ALL_FEATURES', 'PHASES', 'TOUR_SEQUENCE']