"""Model exports for the core app.

This module may be imported even when ``apps.core`` is not present in
``INSTALLED_APPS`` (for example by abstract dependencies like ``Ownable``).
Concrete model imports are therefore guarded to avoid startup errors.
"""

from .ownable import (
    OwnedObjectLink,
    Ownable,
    get_owned_objects_for_group,
    get_owned_objects_for_user,
    get_ownable_models,
)

__all__ = [
    "OwnedObjectLink",
    "Ownable",
    "get_owned_objects_for_group",
    "get_owned_objects_for_user",
    "get_ownable_models",
]

try:
    from .admin_notice import AdminNotice
    from .email import EmailArtifact, EmailTransaction, EmailTransactionAttachment
    from .invite_lead import InviteLead
    from .usage_event import UsageEvent
    from apps.cards.models import RFID
except RuntimeError:
    # Core concrete models are unavailable unless apps.core is installed.
    pass
else:
    __all__.extend(
        [
            "AdminNotice",
            "EmailArtifact",
            "EmailTransaction",
            "EmailTransactionAttachment",
            "InviteLead",
            "UsageEvent",
            "RFID",
        ]
    )
