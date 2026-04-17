from __future__ import annotations

from contextlib import contextmanager
from threading import local
from typing import Dict, Optional, Type

from django.db import models

_thread = local()


def set_context(context: Dict[Type[models.Model], str]) -> None:
    _thread.context = context


def get_context() -> Dict[Type[models.Model], str]:
    return getattr(_thread, "context", {})


def set_request(request) -> None:
    _thread.request = request


def get_request() -> Optional[object]:
    return getattr(_thread, "request", None)


def clear_context() -> None:
    if hasattr(_thread, "context"):
        delattr(_thread, "context")


def clear_request() -> None:
    if hasattr(_thread, "request"):
        delattr(_thread, "request")


@contextmanager
def bind(request=None, current: models.Model | None = None):
    """Temporarily bind request/current model for sigil resolution."""

    previous_context = dict(get_context())
    previous_request = get_request()

    next_context = dict(previous_context)
    if current is not None and getattr(current, "pk", None) is not None:
        next_context[current.__class__] = str(current.pk)

    set_context(next_context)
    if request is not None:
        set_request(request)

    try:
        yield
    finally:
        if previous_context:
            set_context(previous_context)
        else:
            clear_context()

        if previous_request is not None:
            set_request(previous_request)
        else:
            clear_request()
