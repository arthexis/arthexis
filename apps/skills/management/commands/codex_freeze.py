from __future__ import annotations

import json
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.skills.codex_budget import monitor_codex_token_budgets
from apps.skills.codex_freeze import (
    check_freeze,
    freeze_status,
    local_freeze_state,
    write_cached_state,
)
from apps.skills.models import CodexFeatureFreeze


class Command(BaseCommand):
    help = "Manage and evaluate Codex Feature Freeze policy."

    def add_arguments(self, parser):
        parser.add_argument(
            "action",
            choices=["start", "end", "status", "check", "monitor"],
        )
        parser.add_argument("--reason", default="", help="Operator-facing freeze reason.")
        parser.add_argument("--until", help="Freeze end timestamp or duration in hours.")
        parser.add_argument("--hours", type=float, help="Freeze duration in hours.")
        parser.add_argument("--json", action="store_true", help="Emit JSON output.")
        parser.add_argument(
            "--hook-json",
            action="store_true",
            help="Emit before_prompt hook JSON only.",
        )
        parser.add_argument(
            "--no-refresh",
            action="store_true",
            help="Do not refresh the remote freeze state during check.",
        )
        parser.add_argument(
            "--timeout",
            type=float,
            default=1.5,
            help="Configured remote freeze-state timeout in seconds.",
        )

    def handle(self, *args, **options):
        action = options["action"]
        if action == "start":
            payload = self._start(options)
        elif action == "end":
            payload = self._end(options)
        elif action == "status":
            payload = freeze_status()
        elif action == "monitor":
            payload = self._monitor()
        else:
            payload = self._check(options)

        if options["hook_json"]:
            self.stdout.write(json.dumps(payload))
        elif options["json"]:
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
        else:
            self._write_text(payload)

    def _start(self, options) -> dict[str, object]:
        reason = options["reason"].strip()
        if not reason:
            raise CommandError("--reason is required when starting a freeze.")
        ends_at = self._resolve_until(options)
        freeze = CodexFeatureFreeze.create_with_retry(
            reason=reason,
            ends_at=ends_at,
        )
        state = local_freeze_state()
        write_cached_state(state)
        state["created"] = freeze.pk
        return state

    def _end(self, _options) -> dict[str, object]:
        now = timezone.now()
        active = CodexFeatureFreeze.objects.filter(
            active=True,
            ends_at__gt=now,
        )
        updated = active.update(active=False, updated_at=now)
        state = local_freeze_state()
        write_cached_state(state)
        state["ended"] = updated
        return state

    def _check(self, options) -> dict[str, object]:
        decision = check_freeze(
            refresh=not options["no_refresh"],
            timeout_seconds=options["timeout"],
        )
        if options["hook_json"]:
            return decision.as_hook_payload()
        return {
            "decision": decision.decision,
            "reason": decision.reason,
            "source": decision.source,
            "error": decision.error,
            "state": decision.state,
        }

    def _monitor(self) -> dict[str, object]:
        results = [result.as_dict() for result in monitor_codex_token_budgets()]
        return {"budgets": results}

    def _resolve_until(self, options):
        until_opt = options["until"]
        hours_opt = options["hours"]
        now = timezone.now()
        if until_opt is not None and hours_opt is not None:
            raise CommandError("Use either --until or --hours, not both.")
        if hours_opt is not None:
            if hours_opt <= 0:
                raise CommandError("--hours must be greater than 0.")
            return now + timedelta(hours=hours_opt)
        if not until_opt:
            raise CommandError("--until or --hours is required when starting a freeze.")
        until = str(until_opt).strip()
        try:
            hours = float(until)
        except ValueError:
            parsed = parse_datetime(until)
            if parsed is None:
                raise CommandError(
                    f"Could not parse --until timestamp: {until}"
                ) from None
            if timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
            if parsed <= now:
                raise CommandError("--until must be in the future.") from None
            return parsed
        if hours <= 0:
            raise CommandError("--until hours value must be greater than 0.")
        return now + timedelta(hours=hours)

    def _write_text(self, payload: dict[str, object]) -> None:
        for key, value in payload.items():
            if isinstance(value, (dict, list)):
                value = json.dumps(value, sort_keys=True)
            self.stdout.write(f"{key}={value}")
