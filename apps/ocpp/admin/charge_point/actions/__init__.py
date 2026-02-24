"""Composition root for charger admin action mixins."""

from .authorization import AuthorizationActionsMixin
from .availability import AvailabilityActionsMixin
from .diagnostics import DiagnosticsActionsMixin
from .general import GeneralActionsMixin
from .remote_control import RemoteControlActionsMixin
from .simulator import SimulatorActionsMixin


class ChargerAdminActionsMixin(
    DiagnosticsActionsMixin,
    AuthorizationActionsMixin,
    AvailabilityActionsMixin,
    RemoteControlActionsMixin,
    SimulatorActionsMixin,
    GeneralActionsMixin,
):
    """Final mixin consumed by charger admin registration."""


__all__ = ["ChargerAdminActionsMixin"]
