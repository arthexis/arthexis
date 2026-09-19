"""Render the app-wide charger fleet report."""

from django.core.management.base import BaseCommand

from apps.ocpp.domain.snapshots import snapshot_chargers
from apps.ocpp.management.charger.render import render_snapshots
from apps.ocpp.management.charger.selection import select_chargers


class Command(BaseCommand):
    help = "Show configured charger fleet state, sessions, and energy totals."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--charger",
            action="append",
            default=[],
            metavar="IDENTITY",
            help="Limit this app-wide report to a charger identity.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Explicitly select all configured chargers.",
        )

    def handle(self, *args, **options) -> None:
        chargers = select_chargers(
            identities=options["charger"],
            select_all=options["all"],
        )
        render_snapshots(self, snapshot_chargers(chargers))
