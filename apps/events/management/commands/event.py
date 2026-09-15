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


def _field(value: str) -> tuple[str, Any]:
    key, separator, raw_value = value.partition("=")
    key = key.strip().replace("-", "_")
    if not separator or not key:
        raise CommandError("--field must use NAME=VALUE")
    return key, _value(raw_value)


class Command(BaseCommand):
    help = "Publish a structured event through the Arthexis event provider."

    def add_arguments(self, parser) -> None:
        parser.add_argument("operation", choices=("publish", "pub"))
        parser.add_argument("event_type")
        parser.add_argument("--queue")
        parser.add_argument(
            "--field",
            action="append",
            default=[],
            metavar="NAME=VALUE",
            help="Event data field; may be repeated.",
        )
        parser.add_argument(
            "--data",
            help="JSON object containing event data.",
        )

    def handle(self, *args, **options):
        data: dict[str, Any] = {}
        if options["data"]:
            try:
                decoded = json.loads(options["data"])
            except json.JSONDecodeError as exc:
                raise CommandError(f"invalid --data JSON: {exc.msg}") from exc
            if not isinstance(decoded, dict):
                raise CommandError("--data must be a JSON object")
            data.update(decoded)
        for item in options["field"]:
            key, value = _field(item)
            data[key] = value

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
