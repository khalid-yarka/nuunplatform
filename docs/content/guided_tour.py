# docs/content/guided_tour.py
# ============================================================
# The guided tour is a curated, linear sequence of steps pulled
# from various features. It is the "first-time user" path.
#
# `order` is auto-assigned by the registry (1..N). Do not
# pass it here.
#
# Every entry must reference a feature key and a step number
# that actually exists in that feature.
# ============================================================

from docs.models import TourEntry


TOUR_SEQUENCE = [
    # ---- 1. Getting started -------------------------------
    TourEntry(order=0, feature_key='register',   step_number=1),
    TourEntry(order=0, feature_key='register',   step_number=2),
    TourEntry(order=0, feature_key='register',   step_number=3),
    TourEntry(order=0, feature_key='register',   step_number=4),
    TourEntry(order=0, feature_key='login',      step_number=1),
    TourEntry(order=0, feature_key='login',      step_number=2),

    # ---- 2. Orientation -----------------------------------
    TourEntry(order=0, feature_key='first-look', step_number=1),
    TourEntry(order=0, feature_key='first-look', step_number=2),
    TourEntry(order=0, feature_key='first-look', step_number=3),

    # ---- 3. First quiz ------------------------------------
    TourEntry(order=0, feature_key='take-a-quiz', step_number=1),
    TourEntry(order=0, feature_key='take-a-quiz', step_number=2),
    TourEntry(order=0, feature_key='take-a-quiz', step_number=3),
    TourEntry(order=0, feature_key='take-a-quiz', step_number=4),
    TourEntry(order=0, feature_key='take-a-quiz', step_number=5),
    TourEntry(order=0, feature_key='take-a-quiz', step_number=6),

    # ---- 4. Library ---------------------------------------
    TourEntry(order=0, feature_key='pdfs',       step_number=1),
    TourEntry(order=0, feature_key='pdfs',       step_number=2),
    TourEntry(order=0, feature_key='pdfs',       step_number=3),

    # ---- 5. Community -------------------------------------
    TourEntry(order=0, feature_key='groups',     step_number=1),
    TourEntry(order=0, feature_key='groups',     step_number=2),
    TourEntry(order=0, feature_key='groups',     step_number=3),

    # ---- 6. Live quiz -------------------------------------
    TourEntry(order=0, feature_key='live-quiz-join', step_number=1),
    TourEntry(order=0, feature_key='live-quiz-join', step_number=3),
    TourEntry(order=0, feature_key='live-quiz-join', step_number=4),
    TourEntry(order=0, feature_key='live-quiz-join', step_number=6),

    # ---- 7. Focus + history -------------------------------
    TourEntry(order=0, feature_key='focus',      step_number=1),
    TourEntry(order=0, feature_key='focus',      step_number=2),
    TourEntry(order=0, feature_key='history',    step_number=1),
    TourEntry(order=0, feature_key='history',    step_number=4),

    # ---- 8. Ranking + account -----------------------------
    TourEntry(order=0, feature_key='leaderboard', step_number=1),
    TourEntry(order=0, feature_key='leaderboard', step_number=2),
    TourEntry(order=0, feature_key='notifications', step_number=1),
    TourEntry(order=0, feature_key='settings',   step_number=1),
    TourEntry(order=0, feature_key='settings',   step_number=2),
    TourEntry(order=0, feature_key='upgrade',    step_number=1),
]