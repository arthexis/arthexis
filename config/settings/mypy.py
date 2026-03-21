"""Settings shim used for Django-aware MyPy runs."""

from __future__ import annotations

import os


os.environ.setdefault("DJANGO_SECRET_KEY", "mypy-secret-key")
os.environ.setdefault("ARTHEXIS_DISABLE_CELERY", "1")

from . import *  # noqa: F401,F403
