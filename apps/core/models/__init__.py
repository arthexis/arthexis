"""Model exports for the core app."""

from .admin_notice import AdminNotice
from .invite_lead import InviteLead
from .lead_base import LeadBase
from .ownable import (
    Ownable,
    OwnedObjectLink,
    get_ownable_models,
    get_owned_objects_for_group,
    get_owned_objects_for_user,
)
from .usage_event import UsageEvent

_EMAIL_COMPAT_EXPORTS = {
    "EmailArtifact",
    "EmailTransaction",
    "EmailTransactionAttachment",
}

__all__ = [
    "AdminNotice",
    "EmailArtifact",
    "EmailTransaction",
    "EmailTransactionAttachment",
    "InviteLead",
    "LeadBase",
    "OwnedObjectLink",
    "Ownable",
    "UsageEvent",
    "get_ownable_models",
    "get_owned_objects_for_group",
    "get_owned_objects_for_user",
]


def __getattr__(name: str):
    if name in _EMAIL_COMPAT_EXPORTS:
        from apps.emails import models as email_models

        value = getattr(email_models, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
