"""Notification handlers for diagnostics, log, and security events.

These adapters group OCPP 1.6/2.x notification actions whose legacy handlers
perform DB side effects such as log request updates and security event
persistence.
"""

from typing import Protocol

from apps.ocpp.payload_types import HandlerPayload, HandlerResponse


class NotificationConsumer(Protocol):
    async def _handle_diagnostics_status_notification_action_legacy(
        self, payload: HandlerPayload, msg_id: str, raw: str | None, text_data: str | None
    ) -> HandlerResponse: ...

    async def _handle_log_status_notification_action_legacy(
        self, payload: HandlerPayload, msg_id: str, raw: str | None, text_data: str | None
    ) -> HandlerResponse: ...

    async def _handle_security_event_notification_action_legacy(
        self, payload: HandlerPayload, msg_id: str, raw: str | None, text_data: str | None
    ) -> HandlerResponse: ...


class NotificationHandler:
    """Adapter grouping notification-oriented OCPP call handlers."""

    def __init__(self, consumer: NotificationConsumer) -> None:
        self.consumer = consumer

    async def handle_diagnostics_status(
        self, payload: HandlerPayload, msg_id: str, raw: str | None, text_data: str | None
    ) -> HandlerResponse:
        """Handle diagnostics status notifications and persist request status."""

        return await self.consumer._handle_diagnostics_status_notification_action_legacy(
            payload, msg_id, raw, text_data
        )

    async def handle_log_status(
        self, payload: HandlerPayload, msg_id: str, raw: str | None, text_data: str | None
    ) -> HandlerResponse:
        """Handle log status notifications and persist log delivery progress."""

        return await self.consumer._handle_log_status_notification_action_legacy(
            payload, msg_id, raw, text_data
        )

    async def handle_security_event(
        self, payload: HandlerPayload, msg_id: str, raw: str | None, text_data: str | None
    ) -> HandlerResponse:
        """Handle security event notifications and persist security events."""

        return await self.consumer._handle_security_event_notification_action_legacy(
            payload, msg_id, raw, text_data
        )
