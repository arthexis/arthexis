"""Control one live OCPP simulator transaction."""

from __future__ import annotations

import asyncio
import json

from django.core.management.base import BaseCommand, CommandError

from apps.ocpp.management.commands.ocpp_simulator import Command as SimulatorCommand
from apps.ocpp.simulator.network import LiveSimulatorError
from apps.ocpp.simulator.worker import send_control


class Command(BaseCommand):
    help = "Start, meter, or stop a transaction on the active simulated charger."

    def add_arguments(self, parser) -> None:
        actions = parser.add_subparsers(dest="action", required=True)

        start = actions.add_parser("start", help="Start one OCPP 1.6 transaction.")
        start.add_argument("id_tag_value", nargs="?")
        start.add_argument("--id-tag")
        start.add_argument("--charger")
        start.add_argument("--connector", type=int, default=1)
        start.add_argument("--meter-start", type=int, default=0)

        meter = actions.add_parser("meter", help="Send MeterValues for the transaction.")
        meter.add_argument("--charger")
        meter.add_argument("--energy-wh", type=float)
        meter.add_argument("--power-w", type=float)
        meter.add_argument("--current-a", type=float)
        meter.add_argument("--voltage-v", type=float)

        stop = actions.add_parser("stop", help="Stop the active OCPP 1.6 transaction.")
        stop.add_argument("--charger")
        stop.add_argument("--meter-stop", type=int)
        stop.add_argument("--reason")

    @staticmethod
    def _require_nonnegative(options, *names: str) -> None:
        for name in names:
            value = options.get(name)
            if value is not None and value < 0:
                raise CommandError(f"--{name.replace('_', '-')} must be zero or greater")

    def handle(self, *args, **options):
        try:
            charger = SimulatorCommand._resolve_active_charger(options.get("charger"))
            action = options["action"]
            if action == "start":
                id_tag = options.get("id_tag") or options.get("id_tag_value")
                if not id_tag:
                    raise CommandError("transaction start requires an idTag")
                if options["connector"] <= 0:
                    raise CommandError("--connector must be a positive integer")
                self._require_nonnegative(options, "meter_start")
                request = {
                    "action": "transaction-start",
                    "id_tag": id_tag,
                    "connector_id": options["connector"],
                    "meter_start": options["meter_start"],
                }
            elif action == "meter":
                self._require_nonnegative(
                    options,
                    "energy_wh",
                    "power_w",
                    "current_a",
                    "voltage_v",
                )
                request = {"action": "meter"}
                for name in ("energy_wh", "power_w", "current_a", "voltage_v"):
                    if options.get(name) is not None:
                        request[name] = options[name]
            else:
                self._require_nonnegative(options, "meter_stop")
                request = {"action": "transaction-stop"}
                if options.get("meter_stop") is not None:
                    request["meter_stop"] = options["meter_stop"]
                if options.get("reason"):
                    request["reason"] = options["reason"]

            result = asyncio.run(send_control(charger, request))
            self.stdout.write(json.dumps(result, sort_keys=True))
        except (LiveSimulatorError, OSError, ValueError, TimeoutError) as exc:
            raise CommandError(str(exc)) from exc
