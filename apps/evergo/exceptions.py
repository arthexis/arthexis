"""Custom exceptions for the Evergo integration app."""


class EvergoAPIError(RuntimeError):
    """Raised when Evergo API authentication fails or responds unexpectedly."""
