"""Control one live OCPP simulator transaction."""

from __future__ import annotations

import asyncio
import json

from django.core.management.base import BaseCommand, CommandError

from apps.ocpp.management.commands.ocpp_simulator import Command as SimulatorCommand
from apps.ocpp.simulator.network import LiveSimulatorError
from apps.ocpp.simulator.worker import send_control


class Command(BaseCommand):
    help = "Start or stop a transaction on the active simulated charger."

    def add_arguments(self, parser) -> None:
        actions = parser.add_subparsers(dest="action", required=True)

        start = actions.add_parser("start", help="Start one OCPP 1.6 transaction.")
        start.add_argument("id_tag_value", nargs="?")
        start.add_argument("--id-tag")
        start.add_argument("--charger")
        start.add_argument("--connector", type=int, default=1)
        start.add_argument("--meter-start", type=int, default=0)

        stop = actions.add_parser("stop", help="Stop the active OCPP 1.6 transaction.")
        stop.add_argument("--charger")
        stop.add_argument("--meter-stop", type=int)
        stop.add_argument("--reason")

    def handle(self, *args, **options):
        try:
            charger = SimulatorCommand._resolve_active_charger(options.get("charger"))
            if options["action"] == "start":
                id_tag = options.get("id_tag") or options.get("id_tag_value")
                if not id_tag:
                    raise CommandError("transaction start requires an idTag")
                if options["connector"] <= 0:
                    raise CommandError("--connector must be a positive integer")
                if options["meter_start"] < 0:
                    raise CommandError("--meter-start must be zero or greater")
                request = {
                    "action": "transaction-start",
                    "id_tag": id_tag,
                    "connector_id": options["connector"],
                    "meter_start": options["meter_start"],
                }
            else:
                meter_stop = options.get("meter_stop")
                if meter_stop is not None and meter_stop < 0:
                    raise CommandError("--meter-stop must be zero or greater")
                request = {"action": "transaction-stop"}
                if meter_stop is not None:
                    request["meter_stop"] = meter_stop
                if options.get("reason"):
                    request["reason"] = options["reason"]

            result = asyncio.run(send_control(charger, request))
            self.stdout.write(json.dumps(result, sort_keys=True))
        except (LiveSimulatorError, OSError, ValueError, TimeoutError) as exc:
            raise CommandError(str(exc)) from exc
