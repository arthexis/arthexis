"""Compatibility imports for mailbox admin classes now owned by apps.emails."""

from apps.emails.admin_impl.emails import (
    EmailCollectorAdmin,
    EmailInboxAdmin,
    EmailSearchForm,
    SETUP_COLLECTOR_TEXT,
)

__all__ = [
    "EmailCollectorAdmin",
    "EmailInboxAdmin",
    "EmailSearchForm",
    "SETUP_COLLECTOR_TEXT",
]
