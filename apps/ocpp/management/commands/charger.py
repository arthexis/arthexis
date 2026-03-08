"""Alias command for single-charger operations on the default base charger."""

from __future__ import annotations

from apps.ocpp.management.commands.chargers import Command as ChargersCommand


class Command(ChargersCommand):
    """Run charger operations against a default base charger when unspecified."""

    help = "Alias for `chargers` that defaults to a base charger selection."

    def handle(self, *args, **options):
        options["default_base"] = True
        return super().handle(*args, **options)
