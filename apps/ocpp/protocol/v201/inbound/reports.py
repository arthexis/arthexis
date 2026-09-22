"""OCPP 2.0.1 monitoring and charging-profile report handlers."""

from collections.abc import Awaitable, Callable

from asgiref.sync import sync_to_async

from apps.ocpp.models import Charger
from apps.ocpp.services.intake import process_report_intake

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
        return await sync_to_async(process_report_intake)(
            charger=self.charger,
            action="NotifyMonitoringReport",
            payload=payload,
        )

    async def report(self, payload: dict[str, object]) -> dict[str, object]:
        return await sync_to_async(process_report_intake)(
            charger=self.charger,
            action="NotifyReport",
            payload=payload,
        )

    async def charging_profiles(self, payload: dict[str, object]) -> dict[str, object]:
        return await sync_to_async(process_report_intake)(
            charger=self.charger,
            action="ReportChargingProfiles",
            payload=payload,
        )
