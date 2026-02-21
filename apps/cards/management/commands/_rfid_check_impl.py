"""Shared implementation for RFID validation and scanning checks."""

import json
import sys
import time

from django.core.management.base import CommandError
from django.db.models import Q

from apps.cards.background_reader import is_configured
from apps.cards.models import RFID, RFIDAttempt
from apps.cards.reader import validate_rfid_value
from apps.cards.rfid_service import service_available
from apps.cards.scanner import scan_sources
from apps.cards.utils import drain_stdin, user_requested_stop


def add_check_arguments(parser, *, include_positional_value: bool = False) -> None:
    """Attach shared check command arguments to a parser."""
    if include_positional_value:
        parser.add_argument("value", help="RFID value to validate")
    else:
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument(
            "--label",
            help="Validate an RFID associated with the given label id or custom label.",
        )
        target.add_argument(
            "--uid",
            help="Validate an RFID by providing the UID value directly.",
        )
        target.add_argument(
            "--scan",
            action="store_true",
            help="Start the RFID scanner and return the first successfully read tag.",
        )

    parser.add_argument(
        "--kind",
        choices=[choice[0] for choice in RFID.KIND_CHOICES],
        help="Optional RFID kind when validating a UID directly.",
    )
    parser.add_argument(
        "--endianness",
        choices=[choice[0] for choice in RFID.ENDIANNESS_CHOICES],
        help="Optional endianness when validating a UID directly.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="How long to wait for a scan before timing out when running non-interactively (seconds).",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print the JSON response.")


def run_check_command(command, options, *, positional_value: str | None = None) -> None:
    """Execute RFID check behavior and write JSON output."""
    if options.get("scan"):
        result = _scan(options)
    elif options.get("label"):
        result = _validate_label(options["label"])
    else:
        uid_value = positional_value if positional_value is not None else options.get("uid")
        result = _validate_uid(
            uid_value,
            kind=options.get("kind"),
            endianness=options.get("endianness"),
        )

    if "error" in result:
        raise CommandError(result["error"])

    dump_kwargs = {"indent": 2, "sort_keys": True} if options.get("pretty", False) else {}
    command.stdout.write(json.dumps(result, **dump_kwargs))


def _validate_uid(value: str | None, *, kind: str | None, endianness: str | None):
    if not value:
        raise CommandError("RFID UID value is required")
    return validate_rfid_value(value, kind=kind, endianness=endianness)


def _validate_label(label_value: str):
    cleaned = (label_value or "").strip()
    if not cleaned:
        raise CommandError("Label value is required")

    query: Q | None = None
    try:
        label_id = int(cleaned)
    except ValueError:
        label_id = None
    else:
        query = Q(label_id=label_id)

    label_query = Q(custom_label__iexact=cleaned)
    query = label_query if query is None else query | label_query

    tag = RFID.objects.filter(query).order_by("label_id").first()
    if tag is None:
        raise CommandError(f"No RFID found for label '{cleaned}'")

    return validate_rfid_value(tag.rfid, kind=tag.kind, endianness=tag.endianness)


def _scan(options):
    timeout = options.get("timeout", 5.0)
    if timeout is None or timeout <= 0:
        raise CommandError("Timeout must be a positive number of seconds")

    result = _scan_via_attempt(timeout) if service_available() else _scan_via_local(timeout)
    if result.get("error"):
        return result
    if not result.get("rfid"):
        if not is_configured() and not service_available():
            return {"error": "RFID scanner not configured or detected"}
        return {"error": "No RFID detected before timeout"}
    return result


def _scan_via_attempt(timeout: float) -> dict:
    interactive = sys.stdin.isatty()
    if interactive:
        print("Press any key to stop scanning.")
        drain_stdin()
    start = time.monotonic()
    latest_id = (
        RFIDAttempt.objects.filter(source=RFIDAttempt.Source.SERVICE)
        .order_by("-pk")
        .values_list("pk", flat=True)
        .first()
    )
    while True:
        if interactive and user_requested_stop():
            return {"error": "Scan cancelled by user"}
        attempt = (
            RFIDAttempt.objects.filter(source=RFIDAttempt.Source.SERVICE, pk__gt=latest_id or 0)
            .order_by("pk")
            .first()
        )
        if attempt:
            payload = dict(attempt.payload or {})
            payload.setdefault("rfid", attempt.rfid)
            if attempt.label_id:
                payload.setdefault("label_id", attempt.label_id)
            return payload
        if not interactive and time.monotonic() - start >= timeout:
            return {"rfid": None, "label_id": None}
        time.sleep(0.2)


def _scan_via_local(timeout: float) -> dict:
    interactive = sys.stdin.isatty()
    if interactive:
        print("Press any key to stop scanning.")
        drain_stdin()
    start = time.monotonic()
    while True:
        if interactive and user_requested_stop():
            return {"error": "Scan cancelled by user"}
        result = scan_sources(timeout=0.2)
        if result.get("rfid") or result.get("error"):
            return result
        if not interactive and time.monotonic() - start >= timeout:
            return {"rfid": None, "label_id": None}
