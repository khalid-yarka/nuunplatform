# ============================================================
# blueprints/live_quiz/__init__.py
# ============================================================
# Live quiz blueprint — package entry point.
#
# The Blueprint object is created FIRST, before any route module
# is imported. The route modules do `from . import live_quiz_bp`
# and get the fully-defined object. No circular import, no
# partial-init surprises.
#
# Every route function name (lobby, create, waiting_room, ...) is
# preserved unchanged from the original live_quiz_bp.py, so
# url_for('live_quiz.<name>') keeps resolving identically. The
# url_prefix is unchanged: /live-quiz.
# ============================================================

import os
import logging

from flask import Blueprint

logger = logging.getLogger(__name__)


# ============================================================
# SINGLE-WORKER SAFETY
# ============================================================
# Live quiz state is held in process memory. Running more than one
# Gunicorn worker would silently split that state across workers.
# This warning fires at import time so it is impossible to miss.
def _check_single_worker():
    try:
        if 'GUNICORN_WORKER' in os.environ:
            logger.warning(
                "Multiple Gunicorn workers detected. Live Quiz state "
                "is per-process and will be inconsistent across workers. "
                "Please set --workers=1."
            )
    except Exception:
        pass


_check_single_worker()


# ============================================================
# BLUEPRINT OBJECT — defined before any submodule import
# ============================================================
live_quiz_bp = Blueprint('live_quiz', __name__, url_prefix='/live-quiz')


# ============================================================
# ROUTE MODULES — imported last so they can bind to live_quiz_bp
# ============================================================
# Import order matches the URL layout: lobby / create / join /
# room / play / react / lifecycle / admin. Each module defines
# only its own route functions — no cross-module re-declaration
# of endpoint names, no duplicated function names.
from . import routes_lobby      # noqa: E402,F401
from . import routes_create     # noqa: E402,F401
from . import routes_join       # noqa: E402,F401
from . import routes_room       # noqa: E402,F401
from . import routes_play       # noqa: E402,F401
from . import routes_react      # noqa: E402,F401
from . import routes_lifecycle  # noqa: E402,F401
from . import routes_admin      # noqa: E402,F401