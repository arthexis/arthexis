"""Custom exceptions for the Evergo integration app."""


class EvergoAPIError(RuntimeError):
    """Raised when Evergo API authentication fails or responds unexpectedly."""


class EvergoPhaseSubmissionError(EvergoAPIError):
    """Raised when a multi-step phase submission fails after partial completion."""

    def __init__(self, phase_name: str, status_code: int, completed_steps: int) -> None:
        """Build a detailed error with step progress metadata."""
        super().__init__(
            f"{phase_name} failed with status {status_code}. Completed {completed_steps}/3 steps."
        )
        self.phase_name = phase_name
        self.status_code = status_code
        self.completed_steps = completed_steps
