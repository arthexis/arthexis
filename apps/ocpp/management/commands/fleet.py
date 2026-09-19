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
        for first, second in (
            ("enabled", "disabled"),
            ("connected", "disconnected"),
            ("charging", "idle"),
        ):
            group = parser.add_mutually_exclusive_group()
            group.add_argument(f"--{first}", action="store_true")
            group.add_argument(f"--{second}", action="store_true")
        parser.add_argument(
            "--detail",
            action="store_true",
            help="Include connector, transaction timing, and energy detail.",
        )

    def handle(self, *args, **options) -> None:
        filters = tuple(
            name
            for name in (
                "enabled",
                "disabled",
                "connected",
                "disconnected",
                "charging",
                "idle",
            )
            if options[name]
        )
        chargers = select_chargers(
            identities=options["charger"],
            select_all=options["all"],
            filters=filters,
        )
        render_snapshots(
            self,
            snapshot_chargers(chargers),
            detail=options["detail"],
        )
