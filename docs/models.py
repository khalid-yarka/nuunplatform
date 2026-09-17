# docs/models.py
# ============================================================
# Immutable data classes for the docs system.
#
# Content files (docs/content/*.py) construct Feature and Step
# objects. Nothing in this file touches Flask, the DB, or any
# service. Pure data.
# ============================================================

from dataclasses import dataclass, field, replace
from typing import Optional, Tuple


# Top-level routes that a feature key must never shadow.
RESERVED_FEATURE_KEYS = frozenset({
    'roadmap',
    'search',
    'tour',
})

VALID_TIERS = frozenset({'free', 'premium', 'pro'})


@dataclass(frozen=True)
class Step:
    """
    One step in a feature walkthrough.

    `mockup` is a key into the template map (see docs/registry.py).
    `hotspots` is a tuple of `data-docs-anchor` names to pulse.
    `zoom` > 1.0 zooms the mockup on the first hotspot in the list.
    """
    title: str                                   # i18n key
    action: str                                  # i18n key
    mockup: str                                  # template id
    hotspots: Tuple[str, ...] = ()
    tip: Optional[str] = None                    # i18n key or None
    zoom: float = 1.0
    try_url: Optional[str] = None                # real platform URL
    number: int = 0                              # assigned by Feature

    def __post_init__(self):
        # Coerce lists/tuples → tuple for hashing / freezing.
        object.__setattr__(self, 'hotspots', tuple(self.hotspots or ()))


@dataclass(frozen=True)
class Feature:
    """
    One user-facing feature = one walkthrough.

    `steps` are re-numbered automatically. Authors should NOT
    pass a `number` on each Step — it will be overwritten.
    """
    key: str                                     # URL slug
    title: str                                   # i18n key
    tagline: str                                 # i18n key
    icon: str                                    # single emoji
    phase: str                                   # roadmap phase key
    tier: str                                    # 'free' | 'premium' | 'pro'
    steps: Tuple[Step, ...] = ()
    prerequisites: Tuple[str, ...] = ()
    related: Tuple[str, ...] = ()

    def __post_init__(self):
        # 1. Renumber steps.
        numbered = tuple(
            replace(s, number=i + 1)
            for i, s in enumerate(self.steps or ())
        )
        object.__setattr__(self, 'steps', numbered)

        # 2. Coerce list fields to tuples.
        object.__setattr__(self, 'prerequisites', tuple(self.prerequisites or ()))
        object.__setattr__(self, 'related', tuple(self.related or ()))

        # 3. Basic sanity — warn but never raise.
        if self.key in RESERVED_FEATURE_KEYS:
            import logging
            logging.getLogger(__name__).warning(
                "Feature key %r collides with a reserved docs route.", self.key
            )
        if self.tier not in VALID_TIERS:
            import logging
            logging.getLogger(__name__).warning(
                "Feature %r has unknown tier %r; falling back to 'free'.",
                self.key, self.tier,
            )
            object.__setattr__(self, 'tier', 'free')

    @property
    def step_count(self) -> int:
        return len(self.steps)

    def get_step(self, n: int) -> Optional[Step]:
        """1-indexed step lookup. Returns None if out of range."""
        if 1 <= n <= len(self.steps):
            return self.steps[n - 1]
        return None


@dataclass(frozen=True)
class RoadmapPhase:
    """
    One phase on the roadmap timeline.
    """
    key: str                                     # e.g. 'phase-1-core'
    number: int                                  # 1, 2, 3, ...
    title: str                                   # i18n key
    summary: str                                 # i18n key
    feature_keys: Tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, 'feature_keys', tuple(self.feature_keys or ()))


@dataclass(frozen=True)
class TourEntry:
    """
    One linear position in the guided tour.
    """
    order: int                                   # 1-indexed, auto-assigned
    feature_key: str
    step_number: int