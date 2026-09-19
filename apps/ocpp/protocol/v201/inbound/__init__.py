"""OCPP 2.0.1 inbound action-family handlers."""

from collections.abc import Awaitable, Callable

from apps.ocpp.models import Charger
from apps.ocpp.protocol.v201.inbound.certificates import CertificateActions
from apps.ocpp.protocol.v201.inbound.notifications import NotificationActions
from apps.ocpp.protocol.v201.inbound.reports import ReportActions
from apps.ocpp.protocol.v201.inbound.sessions import SessionActions
from apps.ocpp.protocol.v201.matrix import INBOUND_ACTIONS as ACTIONS

Handler = Callable[[dict[str, object]], Awaitable[dict[str, object]]]


class InboundActions:
    """Resolve every retained OCPP 2.0.1 inbound handler by action name."""

    def __init__(self, charger: Charger) -> None:
        certificate_actions = CertificateActions(charger)
        notification_actions = NotificationActions(charger)
        report_actions = ReportActions(charger)
        session_actions = SessionActions(charger)
        self._handlers: dict[str, Handler] = {
            **certificate_actions.handlers,
            **notification_actions.handlers,
            **report_actions.handlers,
            **session_actions.handlers,
        }

    def resolve(self, action: str) -> Handler | None:
        return self._handlers.get(action)
