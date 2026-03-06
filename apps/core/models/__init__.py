"""Model exports for the core app.

This module may be imported even when ``apps.core`` is not present in
``INSTALLED_APPS`` (for example by abstract dependencies like ``Ownable``).

Core-owned concrete models are exported eagerly when ``apps.core`` is
installed. Cross-app optional dependencies are guarded separately so they
cannot mask core model exports.
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
except RuntimeError as exc:
    if "isn't in an application in INSTALLED_APPS" not in str(exc):
        raise
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
        ]
    )

try:
    from apps.cards.models import RFID
except RuntimeError as exc:
    if "isn't in an application in INSTALLED_APPS" not in str(exc):
        raise
    # Optional cross-app model unavailable when apps.cards is not installed.
    pass
else:
    __all__.append("RFID")
