"""Final ChargerAdmin orchestration module."""

from ..common_imports import *
from .base import ChargerAdminBaseMixin
from .diagnostics import ChargerDiagnosticsMixin
from .metrics import ChargerMetricsMixin
from .remote_actions import ChargerRemoteActionsMixin
from .rfid import ChargerRFIDMixin
from .simulator import ChargerSimulatorMixin


@admin.register(Charger)
class ChargerAdmin(
    ChargerDiagnosticsMixin,
    ChargerRemoteActionsMixin,
    ChargerRFIDMixin,
    ChargerSimulatorMixin,
    ChargerMetricsMixin,
    ChargerAdminBaseMixin,
    OwnableAdminMixin,
    EntityModelAdmin,
):
    """Composed charger admin with methods split by concern."""

    pass
