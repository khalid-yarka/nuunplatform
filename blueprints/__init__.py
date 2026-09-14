# ============================================================
# blueprints/__init__.py
# ============================================================
# This file is intentionally empty.
#
# Every blueprint is imported directly from its submodule by
# app.py, e.g.:
#
#     from blueprints.auth_bp import auth_bp
#     from blueprints.admin   import register_admin_blueprints
#
# Importing blueprints here as a side-effect would be fragile:
# any missing file would crash the app at the wrong layer with
# a confusing traceback. Keeping this file empty means the
# package initializes cleanly and each import is the single
# source of truth for where a blueprint lives.
# ============================================================