"""Model exports for the core app."""

from importlib import import_module

from django.apps import apps as django_apps

from .ownable import (
    Ownable,
    OwnedObjectLink,
    get_ownable_models,
    get_owned_objects_for_group,
    get_owned_objects_for_user,
)

_COMPAT_EXPORTS = {
    "AdminNotice": ("apps.ops.admin_notice", "AdminNotice", "apps.ops"),
    "EmailArtifact": ("apps.emails.models", "EmailArtifact", "apps.emails"),
    "EmailTransaction": ("apps.emails.models", "EmailTransaction", "apps.emails"),
    "EmailTransactionAttachment": (
        "apps.emails.models",
        "EmailTransactionAttachment",
        "apps.emails",
    ),
    "InviteLead": ("apps.sites.models", "InviteLead", "apps.sites"),
    "LeadBase": ("apps.sites.models", "LeadBase", "apps.sites"),
    "UsageEvent": ("apps.analytics.models", "UsageEvent", "apps.analytics"),
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
    target = _COMPAT_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attribute, required_app = target
    if not django_apps.is_installed(required_app):
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}; "
            f"{required_app} is not installed"
        )

    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value
