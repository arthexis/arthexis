# APP_STRUCTURE: backend-only (intentionally omits views.py, urls.py, and routes.py)

from .streams import aemit_event, emit_event

__all__ = ["aemit_event", "emit_event"]
