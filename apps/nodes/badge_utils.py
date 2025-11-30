from dataclasses import dataclass
from typing import Any


@dataclass
class BadgeCounterResult:
    """Resolved values for a dashboard badge counter."""

    primary: Any
    secondary: Any | None = None
    label: str | None = None
