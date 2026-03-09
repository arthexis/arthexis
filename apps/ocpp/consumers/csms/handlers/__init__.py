"""Grouped CSMS handler mixins."""

from apps.ocpp.consumers.csms.handlers.availability import AvailabilityHandlersMixin
from apps.ocpp.consumers.csms.handlers.firmware import FirmwareHandlersMixin
from apps.ocpp.consumers.csms.handlers.metering import MeteringHandlersMixin
from apps.ocpp.consumers.csms.handlers.notifications import NotificationHandlersMixin
from apps.ocpp.consumers.csms.handlers.status import StatusHandlersMixin

__all__ = [
    "AvailabilityHandlersMixin",
    "FirmwareHandlersMixin",
    "MeteringHandlersMixin",
    "NotificationHandlersMixin",
    "StatusHandlersMixin",
]
