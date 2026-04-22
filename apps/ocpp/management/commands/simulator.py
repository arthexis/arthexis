"""Management command for controlling OCPP simulator runtime slots."""

from __future__ import annotations

import json
from argparse import BooleanOptionalAction
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.simulators.evcs import _start_simulator, _stop_simulator, get_simulator_state
from apps.simulators.presets import get_simulator_preset, get_simulator_preset_names
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
        parser.add_argument(
            "--preset",
            default="default",
            help="Named simulator preset to seed runtime parameters.",
        )
        parser.add_argument(
            "--preset-override",
            action="append",
            default=[],
            metavar="KEY=VALUE",
            help="Override a preset key before start. Repeat for multiple values.",
        )
        parser.add_argument(
            "--list-presets",
            action="store_true",
            default=False,
            help="List available simulator presets and exit.",
        )

        parser.add_argument("--host", default=None, help="WebSocket host.")
        parser.add_argument("--ws-port", type=int, default=None, help="WebSocket port.")
        parser.add_argument("--cp-path", default=None, help="Charge point path.")
        parser.add_argument(
            "--serial-number",
            default=None,
            help="Charge point serial number.",
        )
        parser.add_argument(
            "--connector-id", type=int, default=None, help="Connector ID."
        )
        parser.add_argument("--rfid", default=None, help="RFID idTag.")
        parser.add_argument("--vin", default=None, help="Vehicle VIN.")
        parser.add_argument(
            "--duration", type=int, default=None, help="Duration in seconds."
        )
        parser.add_argument(
            "--interval", type=float, default=None, help="Polling interval in seconds."
        )
        parser.add_argument(
            "--pre-charge-delay",
            type=float,
            default=None,
            help="Delay before charging starts in seconds.",
        )
        parser.add_argument(
            "--average-kwh", type=float, default=None, help="Average kWh target."
        )
        parser.add_argument(
            "--amperage", type=float, default=None, help="Charging amperage."
        )
        parser.add_argument(
            "--repeat",
            action=BooleanOptionalAction,
            default=None,
            help="Repeat charging sessions forever.",
        )
        parser.add_argument(
            "--username", default=None, help="Optional HTTP Basic auth username."
        )
        parser.add_argument(
            "--password", default=None, help="Optional HTTP Basic auth password."
        )
        parser.add_argument(
            "--backend",
            default=None,
            help="Simulator backend override (arthexis or mobilityhouse when enabled).",
        )
        parser.add_argument(
            "--simulator-name", default=None, help="Friendly simulator name."
        )
        parser.add_argument(
            "--start-delay",
            type=float,
            default=None,
            help="Delay before connection starts.",
        )
        parser.add_argument(
            "--reconnect-slots",
            default=None,
            help="Comma-separated reconnect slots or integer slot count.",
        )
        parser.add_argument(
            "--demo-mode",
            action=BooleanOptionalAction,
            default=None,
            help="Enable demo-mode behavior.",
        )
        parser.add_argument(
            "--meter-interval",
            type=float,
            default=None,
            help="MeterValues reporting interval in seconds.",
        )
        parser.add_argument(
            "--allow-private-network",
            action=BooleanOptionalAction,
            default=None,
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
        if options["list_presets"]:
            self.stdout.write("\n".join(get_simulator_preset_names()))
            return

        if action == "status":
            status = get_simulator_state(cp=slot, refresh_file=True)
            self.stdout.write(json.dumps(status, indent=2, sort_keys=True))
            return

        if action == "stop":
            _stop_simulator(slot)
            self.stdout.write(
                self.style.SUCCESS(f"Simulator slot {slot} stop requested.")
            )
            return

        backend = self._resolve_backend(options.get("backend"))
        params = self._build_start_params(options)
        params.update(
            {
                "simulator_backend": backend,
                "name": options["simulator_name"] or "Simulator",
                "delay": params["start_delay"],
            }
        )
        started, status, log_file = _start_simulator(params, cp=slot)
        if started:
            self.stdout.write(self.style.SUCCESS(status))
        else:
            raise CommandError(status)
        self.stdout.write(f"log_file={log_file}")

    def _build_start_params(self, options: dict[str, Any]) -> dict[str, Any]:
        """Build simulator params from preset defaults and CLI overrides."""

        preset_name = str(options.get("preset") or "default")
        try:
            params = get_simulator_preset(preset_name)
        except ValueError as exc:
            available = ", ".join(get_simulator_preset_names())
            raise CommandError(f"{exc} Available presets: {available}") from exc

        for override in options.get("preset_override") or []:
            key, value = self._parse_preset_override(override, params)
            params[key] = value

        option_key_map = {
            "host": "host",
            "ws_port": "ws_port",
            "cp_path": "cp_path",
            "serial_number": "serial_number",
            "connector_id": "connector_id",
            "rfid": "rfid",
            "vin": "vin",
            "duration": "duration",
            "interval": "interval",
            "pre_charge_delay": "pre_charge_delay",
            "average_kwh": "average_kwh",
            "amperage": "amperage",
            "repeat": "repeat",
            "username": "username",
            "password": "password",
            "start_delay": "start_delay",
            "reconnect_slots": "reconnect_slots",
            "demo_mode": "demo_mode",
            "meter_interval": "meter_interval",
            "allow_private_network": "allow_private_network",
            "ws_scheme": "ws_scheme",
            "use_tls": "use_tls",
        }
        for option_name, param_name in option_key_map.items():
            value = options.get(option_name)
            if value is not None:
                params[param_name] = value

        params["serial_number"] = params.get("serial_number") or params.get("cp_path")
        return params

    def _parse_preset_override(
        self,
        raw_override: str,
        params: dict[str, Any],
    ) -> tuple[str, Any]:
        """Parse ``KEY=VALUE`` preset override and cast using preset types."""

        if "=" not in raw_override:
            raise CommandError(
                f"Invalid --preset-override value '{raw_override}'. Use KEY=VALUE."
            )
        key, value = [part.strip() for part in raw_override.split("=", 1)]
        if not key:
            raise CommandError(
                f"Invalid --preset-override value '{raw_override}'. Missing key."
            )
        if key not in params:
            raise CommandError(f"Unsupported preset override key '{key}'.")
        return key, self._coerce_override_value(key, value, params[key])

    def _coerce_override_value(self, key: str, raw_value: str, existing: Any) -> Any:
        """Cast preset override values using existing preset value types."""

        if existing is None:
            if raw_value.lower() in {"none", "null"}:
                return None
            if key in {"ws_scheme"}:
                return raw_value
            if key in {"use_tls"}:
                return self._parse_bool(raw_value)
            return raw_value
        if isinstance(existing, bool):
            return self._parse_bool(raw_value)
        if isinstance(existing, int) and not isinstance(existing, bool):
            return int(raw_value)
        if isinstance(existing, float):
            return float(raw_value)
        return raw_value

    def _parse_bool(self, value: str) -> bool:
        """Return bool from common truthy/falsy string values."""

        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        raise CommandError(f"Invalid boolean value '{value}'.")

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
