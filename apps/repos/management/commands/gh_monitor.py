"""Configure and run GitHub Monitoring."""

from __future__ import annotations

import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.repos import github_monitor


class Command(BaseCommand):
    help = (
        "Evaluate, configure, poll, and update the local GitHub Monitoring "
        "operator queue."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit machine-readable JSON output.",
        )
        subparsers = parser.add_subparsers(dest="action", required=True)

        subparsers.add_parser(
            "evaluate",
            help="Report whether this node is ready to run GitHub Monitoring.",
        )

        configure_parser = subparsers.add_parser(
            "configure",
            help="Create the default monitor tasks and policy skills.",
        )
        configure_parser.add_argument(
            "--repo",
            default=github_monitor.DEFAULT_REPOSITORY,
            help="Repository slug in owner/name form.",
        )
        configure_parser.add_argument(
            "--codex-command",
            default="codex",
            help="Command used when launching an operator terminal.",
        )
        configure_parser.add_argument(
            "--inactivity-timeout-minutes",
            type=int,
            default=45,
            help="Minutes without heartbeat before a monitor terminal is closed.",
        )
        configure_parser.add_argument(
            "--write",
            action="store_true",
            help="Persist the configuration. Without this flag the command only plans.",
        )

        poll_parser = subparsers.add_parser(
            "poll",
            help="Run one monitor cycle.",
        )
        poll_parser.add_argument(
            "--no-launch",
            action="store_true",
            help="Poll GitHub and maintain active state without launching a terminal.",
        )

        heartbeat_parser = subparsers.add_parser(
            "heartbeat",
            help="Record activity for a monitor item.",
        )
        self._add_item_args(heartbeat_parser)

        complete_parser = subparsers.add_parser(
            "complete",
            help="Mark a monitor item complete.",
        )
        self._add_item_args(complete_parser)

        dismiss_parser = subparsers.add_parser(
            "dismiss",
            help="Dismiss a monitor item without launching more work for it.",
        )
        self._add_item_args(dismiss_parser)

    def handle(self, *args, **options) -> None:
        action = str(options["action"])
        try:
            result = self._run_action(action, options)
        except Exception as exc:
            raise CommandError(str(exc)) from exc
        self._write_result(result, json_output=bool(options.get("json")))

    @staticmethod
    def _add_item_args(parser) -> None:
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--item", type=int, help="Monitor item primary key.")
        group.add_argument(
            "--fingerprint", default="", help="Monitor item fingerprint."
        )

    def _run_action(self, action: str, options: dict[str, Any]) -> Any:
        if action == "evaluate":
            return github_monitor.evaluate_readiness()
        if action == "configure":
            return github_monitor.configure_default_monitoring(
                repository=str(
                    options.get("repo") or github_monitor.DEFAULT_REPOSITORY
                ),
                codex_command=str(options.get("codex_command") or "codex"),
                inactivity_timeout_minutes=int(
                    options.get("inactivity_timeout_minutes") or 45
                ),
                write=bool(options.get("write")),
            )
        if action == "poll":
            return github_monitor.run_monitor_cycle(
                launch=not bool(options.get("no_launch"))
            )
        if action == "heartbeat":
            item = github_monitor.record_activity(
                item_id=options.get("item"),
                fingerprint=str(options.get("fingerprint") or ""),
            )
            return {
                "item": item.pk,
                "status": item.status,
                "last_activity_at": item.last_activity_at.isoformat(),
            }
        if action == "complete":
            item = github_monitor.complete_item(
                item_id=options.get("item"),
                fingerprint=str(options.get("fingerprint") or ""),
            )
            return {"item": item.pk, "status": item.status}
        if action == "dismiss":
            item = github_monitor.dismiss_item(
                item_id=options.get("item"),
                fingerprint=str(options.get("fingerprint") or ""),
            )
            return {"item": item.pk, "status": item.status}
        raise CommandError(f"Unsupported action: {action}")

    def _write_result(self, result: Any, *, json_output: bool) -> None:
        if json_output:
            self.stdout.write(json.dumps(result, indent=2, sort_keys=True, default=str))
            return
        if isinstance(result, dict):
            for key, value in result.items():
                self.stdout.write(f"{key}: {value}")
            return
        self.stdout.write(str(result))
