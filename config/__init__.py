"""Config package initialization."""

from __future__ import annotations


def __getattr__(name: str):
    """Lazily expose selected config package attributes.

    Parameters
    ----------
    name:
        Attribute requested from this package module.

    Returns
    -------
    object
        The resolved attribute value.

    Raises
    ------
    AttributeError
        If *name* is not a supported lazy export.
    """

    if name == "celery":
        from .celery import app as celery_app

        return celery_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ("celery",)
