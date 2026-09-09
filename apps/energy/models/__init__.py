from django.conf import settings

from .billing import CustomerAccount, EnergyTariff, EnergyTariffManager, Location
from .transactions import EnergyTransaction, generate_missing_reports

__all__ = [
    "CustomerAccount",
    "EnergyTariff",
    "EnergyTariffManager",
    "Location",
    "EnergyTransaction",
    "generate_missing_reports",
]

if getattr(settings, "CELERY_RUNTIME_ENABLED", True):
    from .reporting import ClientReport
    from .scheduling import ClientReportSchedule

    __all__.extend(["ClientReportSchedule", "ClientReport"])
else:

    class ClientReport:
        """Terminal-safe facade for Celery-backed reporting."""

        @staticmethod
        def build_rows(*args, **kwargs):
            raise RuntimeError(
                "Client reporting is unavailable on Terminal nodes; "
                "upgrade this node to Control, Satellite, or Watchtower to enable it."
            )

    __all__.append("ClientReport")
