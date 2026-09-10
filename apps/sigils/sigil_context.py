from __future__ import annotations

from threading import local
from typing import Optional

from django.db import models

_thread = local()


def set_context(context: dict[type[models.Model], str]) -> None:
    _thread.context = context


def get_context() -> dict[type[models.Model], str]:
    return getattr(_thread, "context", {})


def set_request(request) -> None:
    _thread.request = request


def get_request() -> object | None:
    return getattr(_thread, "request", None)


def clear_context() -> None:
    if hasattr(_thread, "context"):
        delattr(_thread, "context")


def clear_request() -> None:
    if hasattr(_thread, "request"):
        delattr(_thread, "request")
