"""Data contracts for live OCPP authorization scenarios."""

from dataclasses import dataclass


@dataclass(frozen=True)
class AuthorizationAttempt:
    """One operator-defined authorization attempt in scenario order."""

    name: str
    id_tag: str
    repeat_of: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("authorization attempt name is required")
        if not self.id_tag.strip():
            raise ValueError("authorization attempt id_tag is required")
        if self.repeat_of is not None and not self.repeat_of.strip():
            raise ValueError("repeat_of must name an earlier attempt")


@dataclass(frozen=True)
class AuthorizationScenario:
    """Ordered authorization inputs plus descriptive policy context."""

    name: str
    attempts: tuple[AuthorizationAttempt, ...]
    policy_context: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("authorization scenario name is required")
        if not self.attempts:
            raise ValueError("authorization scenario requires at least one attempt")

        seen: set[str] = set()
        for attempt in self.attempts:
            if attempt.name in seen:
                raise ValueError(
                    f"authorization attempt name must be unique: {attempt.name}"
                )
            if attempt.repeat_of is not None and attempt.repeat_of not in seen:
                raise ValueError(
                    f"repeat_of must reference an earlier attempt: {attempt.repeat_of}"
                )
            seen.add(attempt.name)


@dataclass(frozen=True)
class AuthorizationAttemptResult:
    """Privacy-safe observed result for one live authorization attempt."""

    scenario: str
    attempt: str
    sequence: int
    status: str
    repeat_of: str | None = None
    policy_context: str | None = None

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ValueError("authorization result sequence starts at 1")
        if not self.status.strip():
            raise ValueError("authorization result status is required")
