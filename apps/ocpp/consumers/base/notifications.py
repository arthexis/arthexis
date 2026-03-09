"""Notification handlers for status, diagnostics, firmware, and security.

These adapters group OCPP 1.6/2.x notification actions whose legacy handlers
perform DB side effects such as firmware deployment state updates, log request
updates, and security event persistence.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from . import CSMSConsumer


class NotificationHandler:
    """Adapter grouping notification-oriented OCPP call handlers."""

    def __init__(self, consumer: "CSMSConsumer") -> None:
        self.consumer = consumer

    async def handle_publish_firmware_status(
        self, payload: dict[str, Any], msg_id: str, raw: str | None, text_data: str | None
    ) -> dict:
        """Handle OCPP 2.x firmware publish notifications with DB updates."""

        return await self.consumer._handle_publish_firmware_status_notification_action_legacy(
            payload, msg_id, raw, text_data
        )

    async def handle_diagnostics_status(
        self, payload: dict[str, Any], msg_id: str, raw: str | None, text_data: str | None
    ) -> dict:
        """Handle diagnostics status notifications and persist request status."""

        return await self.consumer._handle_diagnostics_status_notification_action_legacy(
            payload, msg_id, raw, text_data
        )

    async def handle_log_status(
        self, payload: dict[str, Any], msg_id: str, raw: str | None, text_data: str | None
    ) -> dict:
        """Handle log status notifications and persist log delivery progress."""

        return await self.consumer._handle_log_status_notification_action_legacy(
            payload, msg_id, raw, text_data
        )

    async def handle_firmware_status(
        self, payload: dict[str, Any], msg_id: str, raw: str | None, text_data: str | None
    ) -> dict:
        """Handle OCPP 1.6 firmware status notifications with deployment writes."""

        return await self.consumer._handle_firmware_status_notification_action_legacy(
            payload, msg_id, raw, text_data
        )

    async def handle_security_event(
        self, payload: dict[str, Any], msg_id: str, raw: str | None, text_data: str | None
    ) -> dict:
        """Handle security event notifications and persist security events."""

        return await self.consumer._handle_security_event_notification_action_legacy(
            payload, msg_id, raw, text_data
        )
