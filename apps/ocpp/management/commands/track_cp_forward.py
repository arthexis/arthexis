from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.ocpp import store
from apps.ocpp.models import CPForwarder, Charger
from apps.nodes.models import Node

DEFAULT_SAMPLE_NAME = "cp_forward_sample.json"
DEFAULT_SCAN_LIMIT = 500


@dataclass(frozen=True)
class ParsedMessage:
    message_type: int | None
    message_id: str | None
    action: str | None
    payload_hash: str | None
    raw: str


@dataclass(frozen=True)
class SamplePayload:
    captured_at: str
    identifier: str | None
    log_file: str | None
    message: dict[str, object]


def _strip_timestamp(entry: str) -> str:
    if len(entry) >= 24 and entry[23] == " ":
        return entry[24:]
    return entry


def _normalize_raw(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("< "):
        return raw[2:].strip()
    if raw.startswith("> "):
        return raw[2:].strip()
    return raw


def _hash_payload(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_ocpp_message(raw: str) -> ParsedMessage:
    normalized = _normalize_raw(raw)
    message_type = None
    message_id = None
    action = None
    payload_hash = _hash_payload(normalized)

    try:
        parsed = json.loads(normalized)
    except json.JSONDecodeError:
        return ParsedMessage(
            message_type=None,
            message_id=None,
            action=None,
            payload_hash=payload_hash,
            raw=normalized,
        )

    if isinstance(parsed, list) and parsed:
        message_type = parsed[0] if isinstance(parsed[0], int) else None
        if len(parsed) > 1:
            message_id = str(parsed[1])
        if message_type == 2 and len(parsed) > 2:
            action = str(parsed[2])

    return ParsedMessage(
        message_type=message_type,
        message_id=message_id,
        action=action,
        payload_hash=payload_hash,
        raw=normalized,
    )


def _iter_recent_entries(identifier: str, *, limit: int) -> Iterable[str]:
    for entry in store.iter_log_entries(identifier, log_type="charger", limit=limit):
        yield entry.text


def _last_entry_for_identifier(identifier: str) -> str | None:
    iterator = store.iter_log_entries(identifier, log_type="charger", limit=1)
    return next((entry.text for entry in iterator), None)


def _latest_charger_log_file() -> Path | None:
    candidates = list(store.LOG_DIR.glob("charger.*.log"))
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _resolve_log_path(identifier: str) -> Path | None:
    return store.resolve_log_path(identifier, log_type="charger")


def _build_sample(identifier: str | None) -> SamplePayload:
    log_file = None
    raw_entry = None

    if identifier:
        raw_entry = _last_entry_for_identifier(identifier)
        log_path = _resolve_log_path(identifier)
        log_file = str(log_path) if log_path else None
    else:
        log_path = _latest_charger_log_file()
        if log_path:
            log_file = str(log_path)
            entry = next(store.iter_file_lines_reverse(log_path, limit=1), None)
            raw_entry = entry

    if raw_entry is None:
        raise CommandError("No charger log entries found to sample.")

    message_text = _strip_timestamp(raw_entry)
    parsed = _parse_ocpp_message(message_text)
    captured_at = timezone.now().isoformat()
    message = {
        "message_type": parsed.message_type,
        "message_id": parsed.message_id,
        "action": parsed.action,
        "payload_hash": parsed.payload_hash,
        "raw_preview": parsed.raw[:240],
    }
    return SamplePayload(
        captured_at=captured_at,
        identifier=identifier,
        log_file=log_file,
        message=message,
    )


def _write_sample(payload: SamplePayload, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "captured_at": payload.captured_at,
                "identifier": payload.identifier,
                "log_file": payload.log_file,
                "message": payload.message,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _load_sample(path: Path) -> SamplePayload:
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except FileNotFoundError:
        raise CommandError(f"Baseline file not found: {path}")
    if not isinstance(data, dict):
        raise CommandError("Baseline file is not valid JSON.")
    message = data.get("message")
    if not isinstance(message, dict):
        raise CommandError("Baseline file is missing the message payload.")
    return SamplePayload(
        captured_at=str(data.get("captured_at") or ""),
        identifier=data.get("identifier"),
        log_file=data.get("log_file"),
        message=message,
    )



def _baseline_match(identifier: str, baseline: SamplePayload, limit: int) -> bool:
    expected_hash = baseline.message.get("payload_hash")
    expected_action = baseline.message.get("action")
    expected_type = baseline.message.get("message_type")

    for entry in _iter_recent_entries(identifier, limit=limit):
        raw = _strip_timestamp(entry)
        parsed = _parse_ocpp_message(raw)
        if expected_hash and parsed.payload_hash != expected_hash:
            continue
        if expected_type is not None and parsed.message_type != expected_type:
            continue
        if expected_action and parsed.action != expected_action:
            continue
        return True
    return False


def _format_timestamp(value: datetime | None) -> str:
    if not value:
        return "—"
    return timezone.localtime(value).strftime("%Y-%m-%d %H:%M:%S")


class Command(BaseCommand):
    help = (
        "Track CP forwarder activity by sampling recent OCPP frames and validating "
        "them against a baseline."
    )

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--charger",
            "--identifier",
            dest="identifier",
            help="Charger identifier (serial or serial::connector).",
        )
        parser.add_argument(
            "--sample",
            nargs="?",
            const="",
            help="Write a compact sample of the latest charger frame to a JSON file.",
        )
        parser.add_argument(
            "--baseline",
            type=Path,
            help="Compare the latest charger frames to a baseline JSON sample file.",
        )
        parser.add_argument(
            "--validate",
            action="store_true",
            help="Report CP forwarder configuration on this node.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=DEFAULT_SCAN_LIMIT,
            help="Maximum number of log entries to scan when validating a baseline.",
        )

    def handle(self, *args, **options) -> None:
        identifier = options.get("identifier")
        sample_target = options.get("sample")
        baseline_path: Path | None = options.get("baseline")
        validate = bool(options.get("validate"))
        limit = int(options.get("limit") or DEFAULT_SCAN_LIMIT)

        if not any([sample_target is not None, baseline_path, validate]):
            raise CommandError("Specify --sample, --baseline, or --validate.")

        if sample_target is not None:
            output = (
                Path(sample_target)
                if sample_target
                else store.LOG_DIR / DEFAULT_SAMPLE_NAME
            )
            payload = _build_sample(identifier)
            _write_sample(payload, output)
            self.stdout.write(f"Sample written to {output}")

        if baseline_path is not None:
            baseline = _load_sample(baseline_path)
            resolved_identifier = identifier or baseline.identifier
            if not resolved_identifier:
                raise CommandError(
                    "Baseline validation requires a charger identifier."
                )
            matched = _baseline_match(resolved_identifier, baseline, limit=limit)
            if matched:
                self.stdout.write(
                    f"Baseline match found for {resolved_identifier} "
                    f"(scanned {limit} entries)."
                )
            else:
                raise CommandError(
                    f"No baseline match found for {resolved_identifier} "
                    f"(scanned {limit} entries)."
                )

        if validate:
            local_node = Node.get_local()
            if local_node:
                self.stdout.write(
                    f"Local node: {local_node} (endpoint={local_node.public_endpoint or '—'})"
                )
            else:
                self.stdout.write("Local node: —")

            forwarders = list(CPForwarder.objects.all().select_related("target_node"))
            if not forwarders:
                self.stdout.write("Forwarders: none configured")
                return

            self.stdout.write(f"Forwarders: {len(forwarders)}")
            for forwarder in forwarders:
                self.stdout.write("")
                self.stdout.write(
                    f"- {forwarder.name or f'Forwarder #{forwarder.pk}'} "
                    f"(target={forwarder.target_node})"
                )
                self.stdout.write(f"  Enabled: {forwarder.enabled}")
                self.stdout.write(f"  Running: {forwarder.is_running}")
                self.stdout.write(
                    f"  Last synced: {_format_timestamp(forwarder.last_synced_at)}"
                )
                self.stdout.write(
                    f"  Last forwarded: {_format_timestamp(forwarder.last_forwarded_at)}"
                )
                self.stdout.write(
                    f"  Last status: {forwarder.last_status or '—'}"
                )
                self.stdout.write(
                    f"  Last error: {forwarder.last_error or '—'}"
                )
                messages = ", ".join(forwarder.get_forwarded_messages()) or "—"
                self.stdout.write(f"  Forwarded messages: {messages}")

                eligible = (
                    Charger.objects.filter(export_transactions=True)
                    .filter(forwarded_to=forwarder.target_node)
                    .count()
                )
                self.stdout.write(f"  Eligible chargers: {eligible}")
