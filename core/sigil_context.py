from __future__ import annotations

from threading import local
from typing import Dict, Type
from django.db import models

_thread = local()


def set_context(context: Dict[Type[models.Model], str]) -> None:
    _thread.context = context


def get_context() -> Dict[Type[models.Model], str]:
    return getattr(_thread, "context", {})


def clear_context() -> None:
    if hasattr(_thread, "context"):
        delattr(_thread, "context")
