"""Transitional import shim for ``apps.locals.user_data``.

Import from ``apps.locals`` or ``apps.locals.api`` instead.
"""

from apps.locals.api import *  # noqa: F403
