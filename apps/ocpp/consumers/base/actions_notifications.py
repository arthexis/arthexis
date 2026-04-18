"""Protocol action mixin for notification-oriented OCPP calls."""

from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel

from .notifications import NotificationHandler


class NotificationActionsMixin:
    """Expose protocol-routed notification action handlers."""

    def _notification_handler(self) -> NotificationHandler:
        """Return notification helper for firmware/log/security event actions."""
        handler = getattr(self, "_cached_notification_handler", None)
        if handler is None:
            handler = NotificationHandler(self)
            self._cached_notification_handler = handler
        return handler

    @protocol_call("ocpp21", ProtocolCallModel.CP_TO_CSMS, "PublishFirmwareStatusNotification")
    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "PublishFirmwareStatusNotification")
    async def _handle_publish_firmware_status_notification_action(self, payload, msg_id, raw, text_data):
        """Route firmware publish notifications through notification handler."""
        return await self._notification_handler().handle_publish_firmware_status(
            payload, msg_id, raw, text_data
        )

    @protocol_call("ocpp16", ProtocolCallModel.CP_TO_CSMS, "DiagnosticsStatusNotification")
    async def _handle_diagnostics_status_notification_action(self, payload, msg_id, raw, text_data):
        """Route diagnostics notifications through notification handler."""
        return await self._notification_handler().handle_diagnostics_status(
            payload, msg_id, raw, text_data
        )

    @protocol_call("ocpp21", ProtocolCallModel.CP_TO_CSMS, "LogStatusNotification")
    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "LogStatusNotification")
    async def _handle_log_status_notification_action(self, payload, msg_id, raw, text_data):
        """Route log notifications through notification handler."""
        return await self._notification_handler().handle_log_status(
            payload, msg_id, raw, text_data
        )

    @protocol_call("ocpp16", ProtocolCallModel.CP_TO_CSMS, "FirmwareStatusNotification")
    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "FirmwareStatusNotification")
    @protocol_call("ocpp21", ProtocolCallModel.CP_TO_CSMS, "FirmwareStatusNotification")
    async def _handle_firmware_status_notification_action(self, payload, msg_id, raw, text_data):
        """Route firmware status notifications through notification handler."""
        return await self._notification_handler().handle_firmware_status(
            payload, msg_id, raw, text_data
        )

    @protocol_call("ocpp21", ProtocolCallModel.CP_TO_CSMS, "SecurityEventNotification")
    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "SecurityEventNotification")
    async def _handle_security_event_notification_action(self, payload, msg_id, raw, text_data):
        """Route security event notifications through notification handler."""
        return await self._notification_handler().handle_security_event(
            payload, msg_id, raw, text_data
        )
