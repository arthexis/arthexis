"""Compatibility imports for mailbox admin classes now owned by apps.emails."""

from apps.emails.admin_impl.emails import (
    SETUP_COLLECTOR_TEXT,
    EmailCollectorAdmin,
    EmailInboxAdmin,
    EmailSearchForm,
)

__all__ = [
    "EmailCollectorAdmin",
    "EmailInboxAdmin",
    "EmailSearchForm",
    "SETUP_COLLECTOR_TEXT",
]
