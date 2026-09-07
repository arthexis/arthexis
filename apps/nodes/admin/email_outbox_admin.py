"""Compatibility import for the EmailOutbox admin now owned by apps.emails."""

from apps.emails.admin_outbox import EmailOutboxAdmin

__all__ = ["EmailOutboxAdmin"]
