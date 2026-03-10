"""Management command for controlling OCPP simulator runtime slots."""

from __future__ import annotations

import json
from argparse import BooleanOptionalAction

from django.core.management.base import BaseCommand, CommandError

from apps.simulators.evcs import _start_simulator, _stop_simulator, get_simulator_state
from apps.simulators.simulator_runtime import (
    ARTHEXIS_BACKEND,
    MOBILITY_HOUSE_BACKEND,
    get_simulator_backend_choices,
)


class Command(BaseCommand):
    """Run, stop, and inspect simulator slots from the command line."""

    help = "Control OCPP simulator slots using the same options as the simulator UI."

    def add_arguments(self, parser) -> None:
        """Register simulator actions and runtime options."""

        parser.add_argument(
            "action",
            choices=("start", "stop", "status"),
            help="Simulator action to perform.",
        )
        parser.add_argument(
            "--slot",
            type=int,
            default=1,
            choices=(1, 2),
            help="Simulator slot number.",
        )

        parser.add_argument("--host", default="127.0.0.1", help="WebSocket host.")
        parser.add_argument("--ws-port", type=int, default=8000, help="WebSocket port.")
        parser.add_argument("--cp-path", default="CP2", help="Charge point path.")
        parser.add_argument(
            "--serial-number",
            default="CP2",
            help="Charge point serial number.",
        )
        parser.add_argument("--connector-id", type=int, default=1, help="Connector ID.")
        parser.add_argument("--rfid", default="FFFFFFFF", help="RFID idTag.")
        parser.add_argument("--vin", default="WP0ZZZ00000000000", help="Vehicle VIN.")
        parser.add_argument("--duration", type=int, default=600, help="Duration in seconds.")
        parser.add_argument("--interval", type=float, default=5.0, help="Polling interval in seconds.")
        parser.add_argument(
            "--pre-charge-delay",
            type=float,
            default=0.0,
            help="Delay before charging starts in seconds.",
        )
        parser.add_argument("--average-kwh", type=float, default=60.0, help="Average kWh target.")
        parser.add_argument("--amperage", type=float, default=90.0, help="Charging amperage.")
        parser.add_argument(
            "--repeat",
            action=BooleanOptionalAction,
            default=False,
            help="Repeat charging sessions forever.",
        )
        parser.add_argument("--username", default="", help="Optional HTTP Basic auth username.")
        parser.add_argument("--password", default="", help="Optional HTTP Basic auth password.")
        parser.add_argument(
            "--backend",
            default=None,
            help="Simulator backend override (arthexis or mobilityhouse when enabled).",
        )
        parser.add_argument("--simulator-name", default="Simulator", help="Friendly simulator name.")
        parser.add_argument("--start-delay", type=float, default=0.0, help="Delay before connection starts.")
        parser.add_argument(
            "--reconnect-slots",
            default=None,
            help="Comma-separated reconnect slots or integer slot count.",
        )
        parser.add_argument(
            "--demo-mode",
            action=BooleanOptionalAction,
            default=False,
            help="Enable demo-mode behavior.",
        )
        parser.add_argument(
            "--meter-interval",
            type=float,
            default=5.0,
            help="MeterValues reporting interval in seconds.",
        )
        parser.add_argument(
            "--allow-private-network",
            action=BooleanOptionalAction,
            default=False,
            help="Allow simulator to target private network hosts.",
        )
        parser.add_argument(
            "--ws-scheme",
            choices=("ws", "wss"),
            default=None,
            help="Explicit websocket scheme override.",
        )
        parser.add_argument(
            "--use-tls",
            action=BooleanOptionalAction,
            default=None,
            help="Force TLS usage when supported by the selected backend.",
        )

    def handle(self, *args, **options):
        """Dispatch the requested simulator action."""

        action = options["action"]
        slot = options["slot"]

        if action == "status":
            status = get_simulator_state(cp=slot, refresh_file=True)
            self.stdout.write(json.dumps(status, indent=2, sort_keys=True))
            return

        if action == "stop":
            _stop_simulator(slot)
            self.stdout.write(self.style.SUCCESS(f"Simulator slot {slot} stop requested."))
            return

        backend = self._resolve_backend(options.get("backend"))
        params = {
            "host": options["host"],
            "ws_port": options["ws_port"],
            "cp_path": options["cp_path"],
            "serial_number": options["serial_number"] or options["cp_path"],
            "connector_id": options["connector_id"],
            "rfid": options["rfid"],
            "vin": options["vin"],
            "duration": options["duration"],
            "interval": options["interval"],
            "pre_charge_delay": options["pre_charge_delay"],
            "average_kwh": options["average_kwh"],
            "amperage": options["amperage"],
            "repeat": options["repeat"],
            "username": options["username"],
            "password": options["password"],
            "allow_private_network": options["allow_private_network"],
            "simulator_backend": backend,
            "name": options["simulator_name"],
            "delay": options["start_delay"],
            "start_delay": options["start_delay"],
            "reconnect_slots": options["reconnect_slots"],
            "demo_mode": options["demo_mode"],
            "meter_interval": options["meter_interval"],
            "ws_scheme": options["ws_scheme"],
            "use_tls": options["use_tls"],
        }
        started, status, log_file = _start_simulator(params, cp=slot)
        if started:
            self.stdout.write(self.style.SUCCESS(status))
        else:
            self.stdout.write(self.style.WARNING(status))
        self.stdout.write(f"log_file={log_file}")

    def _resolve_backend(self, requested: str | None) -> str:
        """Validate backend choice against enabled simulator backends."""

        backend_choices = get_simulator_backend_choices()
        available_values = [value for value, _label in backend_choices]
        if not available_values:
            raise CommandError("No simulator backends are enabled.")

        if requested:
            normalized = requested.strip().lower()
            if normalized not in available_values:
                raise CommandError(
                    "Unsupported backend %r. Enabled backends: %s"
                    % (requested, ", ".join(sorted(available_values)))
                )
            return normalized

        if MOBILITY_HOUSE_BACKEND in available_values:
            return MOBILITY_HOUSE_BACKEND
        if ARTHEXIS_BACKEND in available_values:
            return ARTHEXIS_BACKEND
        return available_values[0]
