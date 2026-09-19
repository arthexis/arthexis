"""OCPP 2.0.1 monitoring and charging-profile report handlers."""

from collections.abc import Awaitable, Callable

from asgiref.sync import sync_to_async

from apps.ocpp.domain.notifications import record_monitoring, record_notification
from apps.ocpp.models import Charger

Handler = Callable[[dict[str, object]], Awaitable[dict[str, object]]]


class ReportActions:
    """Persist retained OCPP 2.0.1 reports and acknowledge their delivery."""

    def __init__(self, charger: Charger) -> None:
        self.charger = charger
        self.handlers: dict[str, Handler] = {
            "NotifyMonitoringReport": self.monitoring_report,
            "NotifyReport": self.report,
            "ReportChargingProfiles": self.charging_profiles,
        }

    async def monitoring_report(self, payload: dict[str, object]) -> dict[str, object]:
        await sync_to_async(record_monitoring)(
            charger=self.charger,
            event_type="NotifyMonitoringReport",
            payload=payload,
        )
        return {}

    async def report(self, payload: dict[str, object]) -> dict[str, object]:
        await sync_to_async(record_monitoring)(
            charger=self.charger,
            event_type="NotifyReport",
            payload=payload,
        )
        return {}

    async def charging_profiles(self, payload: dict[str, object]) -> dict[str, object]:
        await sync_to_async(record_notification)(
            charger=self.charger,
            action="ReportChargingProfiles",
            payload=payload,
        )
        return {}
