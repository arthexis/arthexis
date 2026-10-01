"""Control one live OCPP simulator transaction."""

from __future__ import annotations

import asyncio
import json

from django.core.management.base import BaseCommand, CommandError

from apps.ocpp.management.commands.ocpp_simulator import Command as SimulatorCommand
from apps.ocpp.simulator.battery import (
    DEFAULT_BATTERY_KWH,
    DEFAULT_DELIVERY_VARIATION,
    DEFAULT_LINE_VOLTAGE_V,
    DEFAULT_MAX_POWER_W,
    DEFAULT_MINIMUM_POWER_FRACTION,
    DEFAULT_TAPER_START_SOC,
)
from apps.ocpp.simulator.network import LiveSimulatorError
from apps.ocpp.simulator.transaction_scenario import (
    SingleTransactionScenario,
    run_single_transaction_scenario,
)
from apps.ocpp.simulator.worker import send_control


class Command(BaseCommand):
    help = "Start, meter, stop, or run a transaction on the active simulated charger."

    def add_arguments(self, parser) -> None:
        actions = parser.add_subparsers(dest="action", required=True)

        start = actions.add_parser("start", help="Start one OCPP 1.6 transaction.")
        start.add_argument("id_tag_value", nargs="?")
        start.add_argument("--id-tag")
        start.add_argument("--charger")
        start.add_argument(
            "--connector",
            type=int,
            help="Use this connector; otherwise choose the lowest-numbered available connector.",
        )
        start.add_argument("--meter-start", type=int)

        meter = actions.add_parser("meter", help="Send MeterValues for the transaction.")
        meter.add_argument("--charger")
        meter.add_argument("--connector", type=int)
        meter.add_argument("--energy-wh", type=float)
        meter.add_argument("--power-w", type=float)
        meter.add_argument("--current-a", type=float)
        meter.add_argument("--voltage-v", type=float)

        stop = actions.add_parser("stop", help="Stop the active OCPP 1.6 transaction.")
        stop.add_argument("--charger")
        stop.add_argument("--connector", type=int)
        stop.add_argument("--meter-stop", type=int)
        stop.add_argument("--reason")

        run = actions.add_parser(
            "run",
            help=(
                "Run one complete charging session. Without --duration, use the "
                "battery/SOC demand model."
            ),
        )
        run.add_argument("id_tag_value", nargs="?")
        run.add_argument("--id-tag")
        run.add_argument("--charger")
        run.add_argument(
            "--duration",
            type=float,
            help="Use legacy wall-clock duration mode instead of battery demand.",
        )
        run.add_argument("--meter-interval", type=float, default=30.0)
        run.add_argument("--connector", type=int)
        run.add_argument("--meter-start", type=int)
        run.add_argument("--power-w", type=float, default=DEFAULT_MAX_POWER_W)
        run.add_argument("--current-a", type=float)
        run.add_argument("--voltage-v", type=float)
        run.add_argument("--reason")
        run.add_argument("--authorization-timeout", type=float)
        run.add_argument(
            "--battery",
            type=float,
            default=DEFAULT_BATTERY_KWH,
            help="Battery capacity in kWh (default: 60).",
        )
        run.add_argument("--start-soc", type=float, default=0.0)
        run.add_argument("--target-soc", type=float, default=100.0)
        run.add_argument("--unplug-soc", type=float)
        run.add_argument("--taper-start-soc", type=float, default=DEFAULT_TAPER_START_SOC)
        run.add_argument(
            "--minimum-power-fraction",
            type=float,
            default=DEFAULT_MINIMUM_POWER_FRACTION,
        )
        run.add_argument(
            "--delivery-variation",
            type=float,
            default=DEFAULT_DELIVERY_VARIATION,
        )
        run.add_argument("--line-voltage", type=float, default=DEFAULT_LINE_VOLTAGE_V)
        run.add_argument("--seed", type=int, default=0)

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
            if action == "run":
                id_tag = options.get("id_tag") or options.get("id_tag_value")
                if not id_tag:
                    raise CommandError("transaction run requires an idTag")
                scenario = SingleTransactionScenario(
                    duration_seconds=options.get("duration"),
                    meter_interval_seconds=options["meter_interval"],
                    connector_id=options.get("connector"),
                    meter_start=options.get("meter_start"),
                    power_w=options["power_w"],
                    current_a=options.get("current_a"),
                    voltage_v=options.get("voltage_v"),
                    stop_reason=options.get("reason"),
                    authorization_timeout=options.get("authorization_timeout"),
                    battery_kwh=options["battery"],
                    start_soc=options["start_soc"],
                    target_soc=options["target_soc"],
                    unplug_soc=options.get("unplug_soc"),
                    taper_start_soc=options["taper_start_soc"],
                    minimum_power_fraction=options["minimum_power_fraction"],
                    delivery_variation=options["delivery_variation"],
                    line_voltage_v=options["line_voltage"],
                    seed=options["seed"],
                )
                result = asyncio.run(
                    run_single_transaction_scenario(charger, id_tag, scenario)
                )
                self.stdout.write(json.dumps(result, sort_keys=True))
                return
            if action == "start":
                id_tag = options.get("id_tag") or options.get("id_tag_value")
                if not id_tag:
                    raise CommandError("transaction start requires an idTag")
                connector = options.get("connector")
                if connector is not None and connector <= 0:
                    raise CommandError("--connector must be a positive integer")
                self._require_nonnegative(options, "meter_start")
                request = {"action": "transaction-start", "id_tag": id_tag}
                if connector is not None:
                    request["connector_id"] = connector
                if options.get("meter_start") is not None:
                    request["meter_start"] = options["meter_start"]
            elif action == "meter":
                self._require_nonnegative(
                    options,
                    "energy_wh",
                    "power_w",
                    "current_a",
                    "voltage_v",
                )
                request = {"action": "meter"}
                if options.get("connector") is not None:
                    request["connector_id"] = options["connector"]
                for name in ("energy_wh", "power_w", "current_a", "voltage_v"):
                    if options.get(name) is not None:
                        request[name] = options[name]
            else:
                self._require_nonnegative(options, "meter_stop")
                request = {"action": "transaction-stop"}
                if options.get("connector") is not None:
                    request["connector_id"] = options["connector"]
                if options.get("meter_stop") is not None:
                    request["meter_stop"] = options["meter_stop"]
                if options.get("reason"):
                    request["reason"] = options["reason"]

            result = asyncio.run(send_control(charger, request))
            self.stdout.write(json.dumps(result, sort_keys=True))
        except (LiveSimulatorError, OSError, ValueError, TimeoutError) as exc:
            raise CommandError(str(exc)) from exc
