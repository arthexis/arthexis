from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class EventContext:
    origin_surface: str
    trace_id: str | None = None
    actor: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EventPolicy:
    requires_approval: bool = False
    security_level: str = "standard"
    validations: tuple[str, ...] = ()


@dataclass(frozen=True)
class CanonicalEvent:
    event_id: str
    timestamp: datetime
    dimension_id: str
    intent: str
    payload: dict[str, Any]
    context: EventContext
    policy: EventPolicy = field(default_factory=EventPolicy)

    @classmethod
    def new(
        cls,
        *,
        dimension_id: str,
        intent: str,
        payload: dict[str, Any],
        origin_surface: str,
        trace_id: str | None = None,
        actor: str | None = None,
        policy: EventPolicy | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "CanonicalEvent":
        if not dimension_id.strip():
            raise ValueError("dimension_id is required")
        if not intent.strip():
            raise ValueError("intent is required")
        if not origin_surface.strip():
            raise ValueError("origin_surface is required")
        context = EventContext(
            origin_surface=origin_surface,
            trace_id=trace_id,
            actor=actor,
            metadata=metadata or {},
        )
        return cls(
            event_id=f"evt_{uuid4().hex}",
            timestamp=datetime.now(tz=timezone.utc),
            dimension_id=dimension_id,
            intent=intent,
            payload=payload,
            context=context,
            policy=policy or EventPolicy(),
        )
