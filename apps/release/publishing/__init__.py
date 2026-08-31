"""Release publish domain and service implementation."""

from .exceptions import DirtyRepository, PublishPending

__all__ = [
    "DirtyRepository",
    "PUBLISH_STEPS",
    "PublishPending",
]


def __getattr__(name: str):
    if name == "PUBLISH_STEPS":
        from .pipeline import PUBLISH_STEPS

        return PUBLISH_STEPS
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
