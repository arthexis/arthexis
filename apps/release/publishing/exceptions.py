"""Domain exceptions for release publishing workflows."""


class DirtyRepository(Exception):
    """Raised when the Git workspace has uncommitted changes."""


class PublishPending(Exception):
    """Raised when publish metadata updates must wait for external publishing."""
