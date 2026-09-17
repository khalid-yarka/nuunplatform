# docs/__init__.py
# ============================================================
# Blueprint entry point. Importing this module registers
# nothing on its own — the app does that explicitly in app.py.
#
#   from docs import docs_bp
#   app.register_blueprint(docs_bp)
# ============================================================

from docs.routes import docs_bp

__all__ = ['docs_bp']