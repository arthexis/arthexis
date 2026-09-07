"""Compatibility imports for email models now owned by apps.emails."""

from apps.emails.models.email import (
    EmailArtifact,
    EmailTransaction,
    EmailTransactionAttachment,
)

__all__ = ["EmailArtifact", "EmailTransaction", "EmailTransactionAttachment"]
