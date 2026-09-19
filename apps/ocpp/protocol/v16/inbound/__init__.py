"""OCPP 1.6 inbound action-family handlers."""

from collections.abc import Awaitable, Callable

from apps.ocpp.models import Charger
from apps.ocpp.protocol.v16.inbound.notifications import NotificationActions
from apps.ocpp.protocol.v16.inbound.sessions import SessionActions

Handler = Callable[[dict[str, object]], Awaitable[dict[str, object]]]


class InboundActions:
    """Resolve the retained OCPP 1.6 inbound handlers by action name."""

    def __init__(self, charger: Charger) -> None:
        session_actions = SessionActions(charger)
        notification_actions = NotificationActions(charger)
        self._handlers: dict[str, Handler] = {
            **session_actions.handlers,
            **notification_actions.handlers,
        }

    def resolve(self, action: str) -> Handler | None:
        return self._handlers.get(action)
