from __future__ import annotations

import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.events.provider import publish_event


def _value(value: str) -> Any:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


class Command(BaseCommand):
    help = "Publish a structured event through the Arthexis event provider."

    def add_arguments(self, parser) -> None:
        parser.add_argument("operation", choices=("publish", "pub"))
        parser.add_argument("event_type")
        parser.add_argument("fields", nargs="*")
        parser.add_argument("--queue")

    def handle(self, *args, **options):
        fields = list(options["fields"])
        data: dict[str, Any] = {}
        index = 0
        while index < len(fields):
            token = fields[index]
            if not token.startswith("--") or token == "--":
                raise CommandError(f"invalid event field: {token}")
            key = token[2:].replace("-", "_")
            if not key:
                raise CommandError("event field name cannot be empty")
            index += 1
            if index >= len(fields) or fields[index].startswith("--"):
                data[key] = True
                continue
            data[key] = _value(fields[index])
            index += 1

        try:
            published = publish_event(
                options["event_type"],
                queue=options["queue"],
                data=data,
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        if not published:
            raise CommandError("event could not be published")

        result = {
            "type": options["event_type"],
            "queue": options["queue"] or options["event_type"],
            "data": data,
            "published": True,
        }
        self.stdout.write(json.dumps(result, separators=(",", ":"), default=str))
        return result
