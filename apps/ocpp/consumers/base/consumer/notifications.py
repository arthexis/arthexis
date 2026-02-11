"""Notification handlers for status, diagnostics, firmware, and security.

These adapters group OCPP 1.6/2.x notification actions whose legacy handlers
perform DB side effects such as firmware deployment state updates, log request
updates, and security event persistence.
"""

from typing import Any


class NotificationHandler:
    """Adapter grouping notification-oriented OCPP call handlers."""

    def __init__(self, consumer: Any) -> None:
        self.consumer = consumer

    async def handle_publish_firmware_status(self, payload, msg_id, raw, text_data):
        """Handle OCPP 2.x firmware publish notifications with DB updates."""

        return await self.consumer._handle_publish_firmware_status_notification_action_legacy(
            payload, msg_id, raw, text_data
        )

    async def handle_diagnostics_status(self, payload, msg_id, raw, text_data):
        """Handle diagnostics status notifications and persist request status."""

        return await self.consumer._handle_diagnostics_status_notification_action_legacy(
            payload, msg_id, raw, text_data
        )

    async def handle_log_status(self, payload, msg_id, raw, text_data):
        """Handle log status notifications and persist log delivery progress."""

        return await self.consumer._handle_log_status_notification_action_legacy(
            payload, msg_id, raw, text_data
        )

    async def handle_firmware_status(self, payload, msg_id, raw, text_data):
        """Handle OCPP 1.6 firmware status notifications with deployment writes."""

        return await self.consumer._handle_firmware_status_notification_action_legacy(
            payload, msg_id, raw, text_data
        )

    async def handle_security_event(self, payload, msg_id, raw, text_data):
        """Handle security event notifications and persist security events."""

        return await self.consumer._handle_security_event_notification_action(
            payload, msg_id, raw, text_data
        )
