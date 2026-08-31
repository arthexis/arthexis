"""Unified LCD management command with subcommands."""

from __future__ import annotations

import os
from contextlib import contextmanager

from django.core.management.base import BaseCommand, CommandError

from apps.screens.management.commands.lcd_actions import (
    animate,
    calibrate,
    debug,
    plan,
    replay,
    write,
)


class Command(BaseCommand):
    """Run LCD tooling via subcommands."""

    help = "LCD utilities (debug, replay, write, animate, plan, calibrate)"

    action_commands = {
        "debug": debug.Command,
        "replay": replay.Command,
        "write": write.Command,
        "animate": animate.Command,
        "plan": plan.Command,
        "calibrate": calibrate.Command,
    }

    def add_arguments(self, parser):
        shared = parser.add_argument_group("shared options")
        shared.add_argument(
            "--device",
            choices=["auto", "pcf8574", "aip31068"],
            default="auto",
            help="Preferred LCD driver/device to use for hardware operations.",
        )
        shared.add_argument(
            "--timing",
            action="append",
            default=[],
            metavar="KEY=VALUE",
            help="Override one or more LCD timing values for this invocation.",
        )
        shared.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse and validate the command, but skip side-effecting behavior where supported.",
        )

        subparsers = parser.add_subparsers(dest="action", required=True)
        for action, command_class in self.action_commands.items():
            subparser = subparsers.add_parser(action)
            command_class().add_arguments(subparser)

    def handle(self, *args, **options):
        action = options.pop("action")
        command_class = self.action_commands.get(action)
        if command_class is None:
            raise CommandError(f"Unknown lcd action: {action}")

        device = options.pop("device", "auto")
        timing_overrides = options.pop("timing", [])
        options.setdefault("dry_run", options.get("dry_run", False))

        with self._lcd_env_overrides(device=device, timing_overrides=timing_overrides):
            command = command_class()
            command.stdout = self.stdout
            command.stderr = self.stderr
            return command.handle(*args, **options)

    @contextmanager
    def _lcd_env_overrides(self, *, device: str, timing_overrides: list[str]):
        previous: dict[str, str | None] = {"LCD_DRIVER": os.getenv("LCD_DRIVER")}
        os.environ["LCD_DRIVER"] = device

        timing_keys = {
            "pulse_enable_delay": "LCD_PULSE_ENABLE_DELAY",
            "pulse_disable_delay": "LCD_PULSE_DISABLE_DELAY",
            "command_delay": "LCD_COMMAND_DELAY",
            "data_delay": "LCD_DATA_DELAY",
            "clear_delay": "LCD_CLEAR_DELAY",
        }
        overridden: list[str] = []
        for override in timing_overrides:
            if "=" not in override:
                raise CommandError("Invalid --timing override. Expected KEY=VALUE.")
            key, value = override.split("=", 1)
            key = key.strip().lower()
            env_key = timing_keys.get(key)
            if not env_key:
                valid = ", ".join(sorted(timing_keys))
                raise CommandError(f"Unsupported timing key '{key}'. Valid keys: {valid}.")
            previous[env_key] = os.getenv(env_key)
            os.environ[env_key] = value.strip()
            overridden.append(env_key)

        try:
            yield
        finally:
            for env_key in ["LCD_DRIVER", *overridden]:
                prior = previous.get(env_key)
                if prior is None:
                    os.environ.pop(env_key, None)
                else:
                    os.environ[env_key] = prior
