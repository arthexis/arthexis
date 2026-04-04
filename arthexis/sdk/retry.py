"""Retry configuration and helpers for transient transport failures."""

from __future__ import annotations

from dataclasses import dataclass, field
from random import random


@dataclass(slots=True)
class RetryPolicy:
    """Configures transient failure retry behavior."""

    max_attempts: int = 3
    base_delay_seconds: float = 0.2
    max_delay_seconds: float = 3.0
    backoff_factor: float = 2.0
    jitter_ratio: float = 0.1
    retryable_status_codes: set[int] = field(
        default_factory=lambda: {408, 425, 429, 500, 502, 503, 504}
    )

    def should_retry_status(self, status_code: int) -> bool:
        """Return whether a given status should be retried."""

        return status_code in self.retryable_status_codes

    def delay_for_attempt(self, attempt: int) -> float:
        """Compute exponential backoff delay for the given 1-indexed attempt."""

        raw_delay = min(
            self.max_delay_seconds,
            self.base_delay_seconds * (self.backoff_factor ** max(0, attempt - 1)),
        )
        jitter = raw_delay * self.jitter_ratio * random()
        return raw_delay + jitter
