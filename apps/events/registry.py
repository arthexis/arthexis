"""In-process event consumer registry used by Celery workers."""

from collections.abc import Callable

from apps.events.models import EventEnvelope

EventHandler = Callable[[EventEnvelope], None]
_handlers: dict[str, list[EventHandler]] = {}


def subscribe(event_type: str, handler: EventHandler) -> None:
    """Register an idempotent handler for one event type."""
    handlers = _handlers.setdefault(event_type, [])
    if handler not in handlers:
        handlers.append(handler)


def dispatch_to_subscribers(envelope: EventEnvelope) -> int:
    """Invoke registered handlers and return the number called."""
    handlers = list(_handlers.get(envelope.event_type, ()))
    for handler in handlers:
        handler(envelope)
    return len(handlers)
