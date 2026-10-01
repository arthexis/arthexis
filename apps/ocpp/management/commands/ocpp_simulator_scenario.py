"""Run reproducible multi-step scenarios against one live OCPP simulator."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.ocpp.management.commands.ocpp_simulator import Command as SimulatorCommand
from apps.ocpp.simulator.network import LiveSimulatorError
from apps.ocpp.simulator.scenario_sequence import load_scenario, run_scenario_sequence


class Command(BaseCommand):
    help = "Run a JSON scenario against the active simulated charger."

    def add_arguments(self, parser) -> None:
        actions = parser.add_subparsers(dest="action", required=True)
        run = actions.add_parser("run", help="Run a multi-step JSON simulator scenario.")
        run.add_argument("scenario")
        run.add_argument("--charger")

    def handle(self, *args, **options):
        try:
            charger = SimulatorCommand._resolve_active_charger(options.get("charger"))
            path = Path(options["scenario"])
            scenario = load_scenario(path)
            result = asyncio.run(run_scenario_sequence(charger, scenario))
            self.stdout.write(json.dumps(result, sort_keys=True))
        except (LiveSimulatorError, OSError, ValueError, TimeoutError) as exc:
            raise CommandError(str(exc)) from exc
