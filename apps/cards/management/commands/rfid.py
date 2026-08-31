from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from apps.cards import rfid_service
from apps.cards.card_commands import command_choices
from apps.cards.classic_layout import normalize_card_name
from apps.cards.command_burn import (
    DEFAULT_COMMAND_CARD_BURN_TIMEOUT,
    CommandCardBurnError,
    resolve_command_card_burn_source,
)
from apps.cards.command_layout import (
    command_payload_digest,
    normalize_command_lifecycle_mode,
    provenance_key_for_reader,
)
from apps.cards.detect import detect_scanner
from apps.cards.initial_profile import InitialProfileError, load_pre_registered_rfids
from apps.cards.models import (
    RFID,
    RFIDAttempt,
    RFIDCommandTemplate,
    RFIDGeneratedLabel,
)
from apps.cards.node_features import RFID_SCANNER_SLUG
from apps.cards.reader import (
    initialize_current_card,
    set_current_card_trait,
    validate_rfid_value,
    write_current_card_command,
    write_current_card_lcd_label,
)
from apps.cards.rfid_import_export import (
    account_column_for_field,
    parse_accounts,
    serialize_accounts,
)
from apps.cards.rfid_names import generated_label_for_rfid
from apps.cards.rfid_service import (
    rfid_scan_lock_path,
    rfid_service_enabled,
    run_service,
    service_available,
    service_endpoint,
)
from apps.cards.scanner import ingest_service_scans, scan_sources
from apps.cards.sync import apply_rfid_payload, serialize_rfid
from apps.cards.utils import drain_stdin, user_requested_stop
from apps.nodes.feature_detection import is_feature_active_for_node
from apps.nodes.models import Node
from apps.printers.printing import (
    DEFAULT_CHUNK_BYTES,
    DEFAULT_CHUNK_DELAY_SECONDS,
    DEFAULT_LABEL_HEIGHT,
    DEFAULT_LABEL_WIDTH,
    DEFAULT_QR_SIZE,
    PHOMEMO_M220_USB_PATH_ENV,
    QRLabelSpec,
    build_phomemo_m220_job,
    build_qr_label_image,
    resolve_phomemo_m220_usb_path,
    write_windows_usb,
)
from utils.loggers.handlers import RFIDFileHandler


class Command(BaseCommand):
    """Canonical command group for RFID operations."""

    help = "RFID command group. Use `rfid <check|watch|service|doctor|init|label|trait|command-card|sync|import|export|pre-register>`."
    DEFAULT_SCAN_TIMEOUT = max(30.0, rfid_service.DEFAULT_SCAN_TIMEOUT)
    DEFAULT_ACTION = "status"
    SYNC_FORMAT = "arthexis.rfid.sync"
    SYNC_VERSION = 1

    def add_arguments(self, parser):
        """Register subcommands and their arguments."""
        subparsers = parser.add_subparsers(dest="action", required=False)

        check_parser = subparsers.add_parser(
            "check", help="Validate RFID tags by UID, label, or scan."
        )
        self._add_check_arguments(check_parser)

        watch_parser = subparsers.add_parser(
            "watch", help="Toggle the always-on RFID watcher."
        )
        watch_parser.add_argument(
            "--stop",
            action="store_true",
            help="Stop the always-on watcher instead of starting it",
        )

        service_parser = subparsers.add_parser(
            "service", help="Run the RFID scanner UDP service."
        )
        self._add_service_arguments(service_parser)

        doctor_parser = subparsers.add_parser("doctor", help="Run RFID diagnostics.")
        self._add_doctor_arguments(doctor_parser)

        init_parser = subparsers.add_parser(
            "init", help="Initialize managed sectors on a presented card."
        )
        self._add_write_arguments(init_parser)

        label_parser = subparsers.add_parser(
            "label", help="Write a sector-0 LCD label to a presented card."
        )
        self._add_label_arguments(label_parser)

        trait_parser = subparsers.add_parser(
            "trait", help="Add or update a trait on a presented card."
        )
        self._add_trait_arguments(trait_parser)

        command_card_parser = subparsers.add_parser(
            "command-card", help="Write or inspect suite command cards."
        )
        command_card_parser.set_defaults(action="command_card")
        self._add_command_card_arguments(command_card_parser)

        sync_parser = subparsers.add_parser(
            "sync", help="Export or import node-transfer RFID JSON bundles."
        )
        sync_parser.set_defaults(action="sync")
        self._add_sync_arguments(sync_parser)

        import_parser = subparsers.add_parser("import", help="Import RFIDs from CSV.")
        self._add_import_arguments(import_parser)

        export_parser = subparsers.add_parser("export", help="Export RFIDs to CSV.")
        self._add_export_arguments(export_parser)

        pre_register_parser = subparsers.add_parser(
            "pre-register",
            help="Create missing RFID rows declared by an initial TOML profile.",
        )
        pre_register_parser.set_defaults(action="pre_register")
        pre_register_parser.add_argument(
            "--profile",
            type=Path,
            required=True,
            help="Initial TOML profile containing [rfid].pre_register values.",
        )

    def handle(self, *args, **options):
        """Dispatch to the selected RFID action."""
        action = options.get("action") or self.DEFAULT_ACTION
        handler = getattr(self, f"_handle_{action}", None)
        if handler is None:
            raise CommandError(f"Unsupported RFID action: {action}")
        handler(options)

    def _handle_status(self, options):
        """Show RFID service runtime state and scanner configuration summary."""
        del options
        endpoint = service_endpoint()
        lock_path = rfid_service.rfid_service_lock_path()
        scanner_lock = rfid_service.rfid_service_lock_path()
        configured = scanner_lock.exists()
        ping = rfid_service.request_service("ping", timeout=0.5)

        self.stdout.write(self.style.MIGRATE_HEADING("RFID Status"))
        self.stdout.write(f"Service endpoint: {endpoint.host}:{endpoint.port}")
        self.stdout.write(
            f"Service lock: {lock_path} ({'present' if lock_path.exists() else 'missing'})"
        )
        self.stdout.write(
            f"Scanner lock: {scanner_lock} ({'present' if scanner_lock.exists() else 'missing'})"
        )
        self.stdout.write(
            "RFID reader configuration: "
            f"{'configured' if configured else 'not configured'}"
        )
        if ping is not None:
            self.stdout.write(self.style.SUCCESS("RFID service state: reachable"))
            return
        self.stdout.write(self.style.WARNING("RFID service state: unreachable"))

    def _handle_pre_register(self, options):
        """Create profile-declared RFID rows without mutating existing rows."""

        profile_path = Path(options["profile"])
        try:
            normalized_rfids = load_pre_registered_rfids(profile_path)
        except InitialProfileError as exc:
            raise CommandError(str(exc)) from exc

        created = 0
        existing = 0
        for rfid in normalized_rfids:
            _card, was_created = RFID.objects.get_or_create(rfid=rfid)
            if was_created:
                created += 1
            else:
                existing += 1
        self.stdout.write(
            f"pre_registered={len(normalized_rfids)} created={created} existing={existing}"
        )

    def _add_check_arguments(self, parser):
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument(
            "--label",
            help="Validate an RFID associated with the given label id or custom label.",
        )
        target.add_argument(
            "--uid", help="Validate an RFID by providing the UID value directly."
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
        parser.add_argument(
            "--no-irq",
            action="store_true",
            help="Bypass IRQ/background-reader path and force direct polling for a scan.",
        )
        parser.add_argument(
            "--pretty", action="store_true", help="Pretty-print the JSON response."
        )

    def _scanner_feature_available(self) -> bool:
        """Return whether rfid-scanner should be available on the local node."""

        node = Node.get_local()
        if node is None:
            return True
        return is_feature_active_for_node(node=node, slug=RFID_SCANNER_SLUG)

    def _handle_check(self, options):
        if options.get("scan"):
            if not self._scanner_feature_available():
                raise CommandError("rfid-scanner feature is not active on this node")
            result = self._scan(options)
        elif options.get("label"):
            result = self._validate_label(options["label"])
        else:
            result = self._validate_uid(
                options.get("uid"),
                kind=options.get("kind"),
                endianness=options.get("endianness"),
            )

        if "error" in result:
            raise CommandError(result["error"])

        dump_kwargs = (
            {"indent": 2, "sort_keys": True} if options.get("pretty", False) else {}
        )
        self.stdout.write(json.dumps(result, **dump_kwargs))

    def _validate_uid(
        self, value: str | None, *, kind: str | None, endianness: str | None
    ):
        if not value:
            raise CommandError("RFID UID value is required")
        return validate_rfid_value(value, kind=kind, endianness=endianness)

    def _validate_label(self, label_value: str):
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

    def _scan(self, options):
        timeout = options.get("timeout", 5.0)
        if timeout is None or timeout <= 0:
            raise CommandError("Timeout must be a positive number of seconds")

        no_irq = options.get("no_irq")
        if no_irq:
            result = self._scan_via_local(timeout, no_irq=True)
        else:
            start = time.monotonic()
            result = self._scan_via_attempt(timeout)
            if (result.get("rfid") is None) and not result.get("error"):
                elapsed = time.monotonic() - start
                remaining = max(0.0, timeout - elapsed)
                if remaining <= 0:
                    result = {"rfid": None, "label_id": None}
                else:
                    result = self._scan_via_local(remaining)
        if result.get("error"):
            return result
        if not result.get("rfid"):
            if not no_irq and not service_available():
                return {"error": "RFID scanner service not configured or detected"}
            return {"error": "No RFID detected before timeout"}
        return result

    def _scan_via_attempt(self, timeout: float) -> dict:
        interactive = sys.stdin.isatty()
        if interactive:
            self.stdout.write("Press any key to stop scanning.")
            drain_stdin()
        self.stdout.flush()
        start = time.monotonic()
        latest_id = (
            RFIDAttempt.objects.filter(source=RFIDAttempt.Source.SERVICE)
            .order_by("-pk")
            .values_list("pk", flat=True)
            .first()
        )
        attempt = None
        while True:
            ingest_service_scans()
            if interactive and user_requested_stop():
                return {"error": "Scan cancelled by user"}
            attempt = (
                RFIDAttempt.objects.filter(
                    source=RFIDAttempt.Source.SERVICE, pk__gt=latest_id or 0
                )
                .order_by("pk")
                .first()
            )
            if attempt:
                break
            if not interactive and time.monotonic() - start >= timeout:
                break
            time.sleep(0.2)
        if not attempt:
            return {"rfid": None, "label_id": None}
        payload = dict(attempt.payload or {})
        payload.setdefault("rfid", attempt.rfid)
        if attempt.label_id:
            payload.setdefault("label_id", attempt.label_id)
        return payload

    def _scan_via_local(self, timeout: float, *, no_irq: bool = False) -> dict:
        interactive = sys.stdin.isatty()
        if interactive:
            self.stdout.write("Press any key to stop scanning.")
            drain_stdin()
        self.stdout.flush()
        start = time.monotonic()
        while True:
            if interactive and user_requested_stop():
                return {"error": "Scan cancelled by user"}
            chunk_timeout = 0.2
            if not interactive:
                remaining = max(0.0, timeout - (time.monotonic() - start))
                if remaining <= 0:
                    return {"rfid": None, "label_id": None}
                chunk_timeout = min(chunk_timeout, remaining)
            result = scan_sources(timeout=chunk_timeout, no_irq=no_irq)
            if result.get("rfid") or result.get("error"):
                return result
            if not interactive and time.monotonic() - start >= timeout:
                return {"rfid": None, "label_id": None}

    def _handle_watch(self, options):
        del options
        raise CommandError(
            "RFID watch mode was removed; run `rfid service` under systemd instead"
        )

    def _add_service_arguments(self, parser):
        endpoint = service_endpoint()
        parser.add_argument(
            "--host",
            default=endpoint.host,
            help="Host interface to bind the RFID service",
        )
        parser.add_argument(
            "--port",
            type=int,
            default=endpoint.port,
            help="UDP port to bind the RFID service",
        )
        parser.add_argument(
            "--debug",
            action=argparse.BooleanOptionalAction,
            default=False,
            help="Enable or disable debug logging for interactive troubleshooting",
        )

    def _handle_service(self, options):
        if not self._scanner_feature_available():
            raise CommandError("rfid-scanner feature is not active on this node")
        host = options.get("host")
        port = options.get("port")
        debug_enabled = options.get("debug", False)
        if debug_enabled:
            self._prepare_debug_service()
        rfid_logger = logging.getLogger("apps.cards.rfid_service")
        rfid_logger.setLevel(logging.DEBUG if debug_enabled else logging.INFO)
        self._configure_rfid_handler(rfid_logger, debug_enabled)
        self.stdout.write(self.style.SUCCESS(f"Starting RFID service on {host}:{port}"))
        run_service(host=host, port=port)

    @staticmethod
    def _configure_rfid_handler(logger: logging.Logger, debug_enabled: bool) -> None:
        level = logging.DEBUG if debug_enabled else logging.INFO
        for handler in logger.handlers:
            if isinstance(handler, RFIDFileHandler):
                handler.setLevel(level)
                return
        handler = RFIDFileHandler(
            filename="rfid.log", when="midnight", backupCount=3, encoding="utf-8"
        )
        handler.setLevel(level)
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        logger.addHandler(handler)

    def _prepare_debug_service(self) -> None:
        base_dir = Path(settings.BASE_DIR)
        lock_dir = base_dir / ".locks"
        feature_enabled = rfid_service_enabled(lock_dir)
        service_name = self._resolve_service_name(lock_dir)
        if not service_name:
            if feature_enabled:
                self.stdout.write(
                    self.style.WARNING(
                        "RFID service feature is enabled, but .locks/service.lck is missing; unable to stop the systemd service before debug start."
                    )
                )
            return
        unit_name = f"rfid-{service_name}.service"
        active = self._systemd_is_active(unit_name)
        if active:
            self._stop_systemd_unit(unit_name)
            return
        if feature_enabled:
            self.stdout.write(
                self.style.WARNING(
                    f"RFID service feature is enabled, but {unit_name} is not active; starting a debug instance."
                )
            )

    def _resolve_service_name(self, lock_dir: Path) -> str | None:
        service_file = lock_dir / "service.lck"
        if not service_file.is_file():
            return None
        service_name = service_file.read_text(encoding="utf-8").strip()
        return service_name or None

    def _systemd_is_active(self, unit_name: str) -> bool | None:
        try:
            result = subprocess.run(
                ["systemctl", "is-active", unit_name],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            self.stdout.write(
                self.style.WARNING(
                    f"systemctl not available; cannot verify {unit_name} status before debug start."
                )
            )
            return None
        status = result.stdout.strip()
        return result.returncode == 0 and status in {
            "active",
            "activating",
            "reloading",
        }

    def _stop_systemd_unit(self, unit_name: str) -> None:
        self.stdout.write(f"Stopping {unit_name} to start debug service...")
        try:
            result = subprocess.run(
                ["systemctl", "stop", unit_name],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            self.stdout.write(
                self.style.WARNING(
                    f"systemctl not available; cannot stop {unit_name} before debug start."
                )
            )
            return
        if result.returncode != 0:
            self.stdout.write(
                self.style.WARNING(
                    f"Failed to stop {unit_name} before debug start: {(result.stderr or '').strip()}"
                )
            )
            return
        self.stdout.write(self.style.SUCCESS(f"Stopped {unit_name}"))

    def _add_doctor_arguments(self, parser):
        parser.add_argument(
            "--timeout",
            type=float,
            default=self.DEFAULT_SCAN_TIMEOUT,
            help="Scan timeout in seconds when running non-interactively (default: %(default)s)",
        )
        parser.add_argument(
            "--scan",
            action="store_true",
            help="Attempt a scan via the RFID service after checks.",
        )
        parser.add_argument(
            "--deep-read",
            action="store_true",
            help="Toggle deep-read mode via the RFID service.",
        )
        parser.add_argument(
            "--no-input", action="store_true", help="Skip interactive prompts."
        )
        parser.add_argument(
            "--show-raw",
            action="store_true",
            help="Show raw RFID values in output (default is masked).",
        )

    def _add_write_arguments(self, parser, *, default_timeout: float = 2.0):
        parser.add_argument(
            "--timeout",
            type=float,
            default=default_timeout,
            help="How long to wait for the presented card.",
        )
        parser.add_argument(
            "--writer-id", help="Optional writer model or node id stored on the card."
        )
        parser.add_argument(
            "--pretty", action="store_true", help="Pretty-print the JSON response."
        )

    def _add_label_arguments(self, parser):
        self._add_write_arguments(parser)
        text = parser.add_mutually_exclusive_group(required=True)
        text.add_argument(
            "--text", help="LCD label text. Use a newline to split the two LCD lines."
        )
        text.add_argument(
            "--line1", help="First LCD line; combine with --line2 for the second line."
        )
        parser.add_argument(
            "--line2", default="", help="Second LCD line when --line1 is used."
        )

    def _add_trait_arguments(self, parser):
        self._add_write_arguments(parser)
        parser.add_argument(
            "--key", required=True, help="Trait key, up to 16 ASCII bytes."
        )
        parser.add_argument(
            "--value", required=True, help="Trait value, up to 80 ASCII bytes."
        )
        parser.add_argument(
            "--no-init",
            action="store_true",
            help="Do not initialize the card before writing the trait.",
        )

    def _add_command_card_arguments(self, parser):
        parser.add_argument(
            "--write-command",
            dest="write_command",
            help="Template name or slug to write to the presented card.",
        )
        parser.add_argument(
            "--list-commands",
            action="store_true",
            help="List available command-card templates.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Include inactive templates when listing available commands.",
        )
        self._add_write_arguments(parser)
        parser.add_argument(
            "--provenance",
            default="",
            help="Optional reader/writer provenance key. Defaults to this node.",
        )
        self._add_command_label_print_arguments(parser, include_flag=True)
        subparsers = parser.add_subparsers(dest="command_card_action", required=False)
        subparsers.add_parser(
            "templates", help="List available command-card templates."
        )
        label_parser = subparsers.add_parser(
            "label",
            help="Print a command-card tracking label for an existing card.",
        )
        label_parser.add_argument(
            "--card",
            required=True,
            help="Existing RFID pk, UID, generated label, custom label, or name key.",
        )
        label_parser.add_argument(
            "--template",
            help="Override command template name or slug. Defaults to the card template.",
        )
        self._add_command_label_print_arguments(label_parser, include_flag=False)
        label_parser.add_argument(
            "--pretty", action="store_true", help="Pretty-print the JSON response."
        )
        burn_parser = subparsers.add_parser(
            "burn",
            help="Burn a selected template or copy the previous scanned command card.",
        )
        burn_parser.add_argument(
            "--template",
            help=(
                "Template name or slug to burn. Defaults to the previous scanned "
                "command card."
            ),
        )
        self._add_write_arguments(
            burn_parser,
            default_timeout=DEFAULT_COMMAND_CARD_BURN_TIMEOUT,
        )
        burn_parser.add_argument(
            "--provenance",
            default="",
            help="Optional reader/writer provenance key. Defaults to this node.",
        )
        self._add_command_label_print_arguments(burn_parser, include_flag=True)
        write_parser = subparsers.add_parser(
            "write", help="Write a command-card payload to the presented card."
        )
        self._add_write_arguments(write_parser)
        write_parser.add_argument(
            "--name", required=True, help="Card name/natural key, up to 16 ASCII bytes."
        )
        write_parser.add_argument(
            "--command",
            required=True,
            choices=[choice[0] for choice in command_choices()],
            help="Suite special command to store on the card.",
        )
        write_parser.add_argument(
            "--params-json",
            default="{}",
            help="JSON object containing command parameters.",
        )
        write_parser.add_argument(
            "--sigils-json", default="{}", help="JSON object containing command sigils."
        )
        write_parser.add_argument(
            "--lifecycle-mode",
            choices=["triggered", "reader_held"],
            default="triggered",
            help="Execution lifecycle mode encoded in command-card metadata.",
        )
        write_parser.add_argument(
            "--provenance",
            default="",
            help="Optional reader/writer provenance key. Defaults to this node.",
        )
        self._add_command_label_print_arguments(write_parser, include_flag=True)

    def _add_command_label_print_arguments(self, parser, *, include_flag: bool) -> None:
        if include_flag:
            parser.add_argument(
                "--print-label",
                action="store_true",
                help=(
                    "Print a QR sticker label for the command tracking view after "
                    "a successful card write."
                ),
            )
        parser.add_argument(
            "--label-printer",
            choices=["none", "phomemo-m220"],
            default="phomemo-m220",
            help="Printer backend for command-card labels.",
        )
        parser.add_argument(
            "--label-dry-run",
            action="store_true",
            help="Build the preview and printer job without writing to USB.",
        )
        parser.add_argument(
            "--label-output",
            help="PNG preview path for the command-card label.",
        )
        parser.add_argument(
            "--label-base-url",
            help="Base URL for the command tracking view. Defaults to PUBLIC_BASE_URL.",
        )
        parser.add_argument("--label-width", type=int, default=DEFAULT_LABEL_WIDTH)
        parser.add_argument("--label-height", type=int, default=DEFAULT_LABEL_HEIGHT)
        parser.add_argument("--label-qr-size", type=int, default=DEFAULT_QR_SIZE)
        parser.add_argument(
            "--usb-path",
            help=(
                "Windows USB device path. Defaults to "
                f"{PHOMEMO_M220_USB_PATH_ENV} or auto-discovery."
            ),
        )
        parser.add_argument("--chunk-bytes", type=int, default=DEFAULT_CHUNK_BYTES)
        parser.add_argument(
            "--chunk-delay", type=float, default=DEFAULT_CHUNK_DELAY_SECONDS
        )
        parser.add_argument("--speed", type=int, default=2)
        parser.add_argument("--density", type=int, default=15)

    def _write_json_result(self, result: dict, *, pretty: bool = False) -> None:
        if result.get("error"):
            raise CommandError(result["error"])
        if result.get("errors"):
            if result.get("initialized") is False:
                raise CommandError("RFID initialization failed")
            raise CommandError("RFID operation failed")
        dump_kwargs = {"indent": 2, "sort_keys": True} if pretty else {}
        self.stdout.write(json.dumps(result, **dump_kwargs))

    def _handle_init(self, options):
        result = initialize_current_card(
            timeout=options["timeout"],
            writer_id=options.get("writer_id"),
        )
        self._write_json_result(result, pretty=options.get("pretty", False))

    def _handle_label(self, options):
        label = options.get("text")
        if label is None:
            label = "\n".join((options.get("line1") or "", options.get("line2") or ""))
        result = write_current_card_lcd_label(
            label=label,
            timeout=options["timeout"],
            writer_id=options.get("writer_id"),
        )
        self._write_json_result(result, pretty=options.get("pretty", False))

    def _handle_trait(self, options):
        result = set_current_card_trait(
            key=options["key"],
            value=options["value"],
            timeout=options["timeout"],
            writer_id=options.get("writer_id"),
            initialize=not options.get("no_init", False),
        )
        self._write_json_result(result, pretty=options.get("pretty", False))

    def _handle_command_card(self, options):
        action = options.get("command_card_action")
        if options.get("write_command"):
            self._handle_command_card_write_template(options)
            return
        if options.get("list_commands") or action in (None, "templates"):
            self._handle_command_card_templates(options)
            return
        if action == "label":
            self._handle_command_card_label(options)
            return
        if action == "burn":
            self._handle_command_card_burn(options)
            return
        if action != "write":
            raise CommandError(f"Unsupported command-card action: {action}")
        try:
            params = json.loads(options.get("params_json") or "{}")
            sigils = json.loads(options.get("sigils_json") or "{}")
        except json.JSONDecodeError as exc:
            raise CommandError(f"Invalid command-card JSON: {exc}") from exc
        if not isinstance(params, dict):
            raise CommandError("--params-json must be a JSON object")
        if not isinstance(sigils, dict):
            raise CommandError("--sigils-json must be a JSON object")
        provenance = options.get("provenance") or provenance_key_for_reader(
            Node.get_local() or ""
        )
        result = write_current_card_command(
            name=options["name"],
            command=options["command"],
            params=params,
            sigils=sigils,
            timeout=options["timeout"],
            writer_id=options.get("writer_id"),
            provenance_key=provenance,
            lifecycle_mode=options.get("lifecycle_mode") or "triggered",
        )
        if (
            options.get("print_label")
            and not result.get("error")
            and not result.get("errors")
        ):
            template = self._ensure_command_template_for_write(
                name=options["name"],
                command=options["command"],
                params=params,
                sigils=sigils,
                lifecycle_mode=options.get("lifecycle_mode") or "triggered",
            )
            result["template"] = template.name
            result["template_url"] = template.get_absolute_url()
            self._attach_command_card_label_print(
                result,
                template=template,
                options=options,
                raise_errors=False,
            )
        self._write_json_result(result, pretty=options.get("pretty", False))

    def _template_queryset(self, *, include_inactive: bool = False):
        queryset = RFIDCommandTemplate.objects.all()
        if not include_inactive:
            queryset = queryset.filter(is_active=True)
        return queryset.order_by("source", "name")

    def _handle_command_card_templates(self, options):
        templates = list(
            self._template_queryset(include_inactive=options.get("all", False))
        )
        if not templates:
            self.stdout.write("No RFID command templates are available.")
            return
        self.stdout.write("Available RFID command templates:")
        for template in templates:
            status = "" if template.is_active else " inactive"
            args = template.command_params.get("args")
            args_text = (
                f" {' '.join(str(arg) for arg in args)}"
                if isinstance(args, list)
                else ""
            )
            suite_command = template.command_params.get("command")
            suite_text = f" -> {suite_command}{args_text}" if suite_command else ""
            self.stdout.write(
                f"  {template.name:<16} {template.command_name}{suite_text} "
                f"[{template.source}{status}] {template.get_absolute_url()}"
            )

    def _get_command_template(self, value: str) -> RFIDCommandTemplate:
        cleaned = (value or "").strip()
        if not cleaned:
            raise CommandError("Command template name is required")
        normalized_name = cleaned.upper()
        template = (
            RFIDCommandTemplate.objects.filter(
                Q(name__iexact=normalized_name) | Q(slug__iexact=cleaned)
            )
            .order_by("source", "name")
            .first()
        )
        if template is None:
            raise CommandError(f"No RFID command template found for '{cleaned}'")
        if not template.is_active:
            raise CommandError(f"RFID command template '{template.name}' is inactive")
        return template

    def _ensure_command_template_for_write(
        self,
        *,
        name: str,
        command: str,
        params: dict,
        sigils: dict,
        lifecycle_mode: str = "triggered",
    ) -> RFIDCommandTemplate:
        normalized_name = normalize_card_name(name)
        normalized_lifecycle_mode = normalize_command_lifecycle_mode(lifecycle_mode)
        expected_digest = command_payload_digest(
            name=normalized_name,
            command=command,
            params=params,
            sigils=sigils,
        )
        template = RFIDCommandTemplate.objects.filter(name=normalized_name).first()
        if template is not None:
            if template.payload_digest != expected_digest:
                raise CommandError(
                    "Existing RFID command template "
                    f"'{template.name}' has a different payload. "
                    "Use --write-command for that template or choose a unique name."
                )
            if template.lifecycle_mode != normalized_lifecycle_mode:
                template.lifecycle_mode = normalized_lifecycle_mode
                template.save(update_fields=["lifecycle_mode"])
            return template
        return RFIDCommandTemplate.objects.create(
            name=normalized_name,
            title=normalized_name.title(),
            description="Created by the command-card burner while printing a label.",
            command_name=command,
            command_params=params,
            command_sigils=sigils,
            lifecycle_mode=normalized_lifecycle_mode,
            source=RFIDCommandTemplate.Source.CUSTOM,
            is_active=True,
        )

    def _handle_command_card_write_template(self, options):
        template = self._get_command_template(options["write_command"])
        result = self._write_command_card_template(template, options)
        self._write_json_result(result, pretty=options.get("pretty", False))

    def _handle_command_card_burn(self, options):
        try:
            source = resolve_command_card_burn_source(options.get("template"))
        except CommandCardBurnError as exc:
            raise CommandError(str(exc)) from exc
        result = self._write_command_card_template(source.template, options)
        result["template_source"] = "selected" if source.selected else "previous_scan"
        if source.source_rfid is not None:
            result["source_label_id"] = source.source_rfid.pk
            result["source_rfid"] = source.source_rfid.rfid
        self._write_json_result(result, pretty=options.get("pretty", False))

    def _write_command_card_template(self, template: RFIDCommandTemplate, options):
        provenance = options.get("provenance") or provenance_key_for_reader(
            Node.get_local() or ""
        )
        result = write_current_card_command(
            name=template.name,
            command=template.command_name,
            params=template.command_params,
            sigils=template.command_sigils,
            timeout=options["timeout"],
            writer_id=options.get("writer_id"),
            provenance_key=provenance,
            lifecycle_mode=template.lifecycle_mode,
        )
        if not result.get("error") and result.get("label_id"):
            tag = RFID.objects.filter(pk=result["label_id"]).first()
            if tag is not None:
                tag.command_template = template
                tag.save(update_fields=["command_template"])
            result["template"] = template.name
            result["template_url"] = template.get_absolute_url()
            result["requires_owner"] = template.requires_owner
            if options.get("print_label"):
                self._attach_command_card_label_print(
                    result,
                    template=template,
                    tag=tag,
                    options=options,
                    raise_errors=False,
                )
        return result

    def _handle_command_card_label(self, options):
        tag = self._get_command_card_label_rfid(options["card"])
        template = self._template_for_command_card_label(
            tag,
            template_override=options.get("template"),
        )
        result = {
            "rfid": tag.rfid,
            "label_id": tag.pk,
            "card_label": self._command_card_sticker_card_label(tag),
            "template": template.name,
            "template_url": template.get_absolute_url(),
            "label_print": self._print_command_card_tracking_label(
                template=template,
                tag=tag,
                options=options,
                raise_errors=True,
            ),
        }
        self._write_json_result(result, pretty=options.get("pretty", False))

    def _get_command_card_label_rfid(self, value: str) -> RFID:
        cleaned = (value or "").strip()
        if not cleaned:
            raise CommandError("Existing card lookup cannot be blank")
        normalized = RFID.normalize_code(cleaned)
        tag = (
            self._lookup_rfid_by_pk(cleaned)
            or self._lookup_rfid_by_exact_code(normalized)
            or self._lookup_rfid_by_custom_label(cleaned)
            or self._lookup_rfid_by_generated_label(cleaned)
            or self._lookup_rfid_by_name_key(normalized)
            or self._lookup_rfid_by_fuzzy_code(normalized)
        )
        if tag is None:
            raise CommandError(f"No RFID card found for '{cleaned}'")
        return tag

    def _lookup_rfid_by_pk(self, cleaned: str) -> RFID | None:
        if not cleaned.isdigit():
            return None
        return RFID.objects.filter(pk=int(cleaned)).first()

    def _lookup_rfid_by_exact_code(self, normalized: str) -> RFID | None:
        if not normalized:
            return None
        return RFID.objects.filter(rfid=normalized).first()

    def _lookup_rfid_by_custom_label(self, cleaned: str) -> RFID | None:
        matches = RFID.objects.filter(custom_label__iexact=cleaned).order_by("pk")[:2]
        return self._single_rfid_card_match(matches, "custom label", cleaned)

    def _lookup_rfid_by_generated_label(self, cleaned: str) -> RFID | None:
        matches = RFID.objects.filter(generated_label__iexact=cleaned).order_by("pk")[
            :2
        ]
        tag = self._single_rfid_card_match(matches, "generated label", cleaned)
        if tag is not None:
            return tag

        allocation = RFIDGeneratedLabel.objects.filter(
            generated_label__iexact=cleaned
        ).first()
        if allocation is not None:
            allocated_matches = RFID.objects.filter(
                name_key=allocation.name_key
            ).order_by("pk")[:2]
            tag = self._single_rfid_card_match(
                allocated_matches,
                "generated label",
                cleaned,
            )
            if tag is not None:
                tag.ensure_generated_label()
                return tag

        cleaned_key = cleaned.casefold()
        legacy_pks = []
        for pk, rfid in (
            RFID.objects.filter(generated_label="", rfid__gt="")
            .values_list("pk", "rfid")
            .iterator()
        ):
            if generated_label_for_rfid(rfid).casefold() != cleaned_key:
                continue
            legacy_pks.append(pk)
            if len(legacy_pks) > 1:
                break

        legacy_matches = list(RFID.objects.filter(pk__in=legacy_pks).order_by("pk"))
        tag = self._single_rfid_card_match(legacy_matches, "generated label", cleaned)
        if tag is not None:
            tag.ensure_generated_label()
        return tag

    def _lookup_rfid_by_name_key(self, normalized: str) -> RFID | None:
        if not normalized:
            return None
        matches = RFID.objects.filter(name_key=normalized).order_by("pk")[:2]
        return self._single_rfid_card_match(matches, "name key", normalized)

    def _lookup_rfid_by_fuzzy_code(self, normalized: str) -> RFID | None:
        if not normalized:
            return None
        return RFID.find_match(normalized)

    def _single_rfid_card_match(
        self, matches, label_kind: str, cleaned: str
    ) -> RFID | None:
        matches = list(matches)
        if len(matches) > 1:
            raise CommandError(
                f"Multiple RFID cards match {label_kind} '{cleaned}'. "
                "Use the numeric RFID pk instead."
            )
        return matches[0] if matches else None

    def _template_for_command_card_label(
        self,
        tag: RFID,
        *,
        template_override: str | None,
    ) -> RFIDCommandTemplate:
        if template_override:
            return self._get_command_template(template_override)
        if tag.command_template_id:
            return tag.command_template
        if tag.command_card_name:
            template = RFIDCommandTemplate.objects.filter(
                name=tag.command_card_name
            ).first()
            if template is not None:
                return template
        raise CommandError(
            "Existing RFID card is not linked to a command template. "
            "Pass --template to choose the tracking view explicitly."
        )

    def _attach_command_card_label_print(
        self,
        result: dict,
        *,
        template: RFIDCommandTemplate,
        options,
        tag: RFID | None = None,
        raise_errors: bool,
    ) -> None:
        if tag is None:
            label_id = result.get("label_id")
            tag = RFID.objects.filter(pk=label_id).first() if label_id else None
        if tag is None:
            result["label_print"] = {
                "printed": False,
                "error": "Card write did not return a persisted RFID label_id.",
            }
            return
        if tag.command_template_id != template.pk:
            tag.command_template = template
            tag.save(update_fields=["command_template"])
        try:
            result["label_print"] = self._print_command_card_tracking_label(
                template=template,
                tag=tag,
                options=options,
                raise_errors=raise_errors,
            )
        except CommandError as exc:
            if raise_errors:
                raise
            result["label_print"] = {"printed": False, "error": str(exc)}

    def _print_command_card_tracking_label(
        self,
        *,
        template: RFIDCommandTemplate,
        tag: RFID,
        options,
        raise_errors: bool,
    ) -> dict:
        payload = self._command_template_tracking_url(
            template,
            base_url=options.get("label_base_url"),
        )
        card_label = self._command_card_sticker_card_label(tag)
        spec = QRLabelSpec(
            width=options["label_width"],
            height=options["label_height"],
            qr_size=options["label_qr_size"],
            title=template.display_title,
            subtitle=f"Card {card_label}",
            footer="Command tracking",
        )
        try:
            image = build_qr_label_image(payload, spec=spec)
        except Exception as exc:
            raise CommandError(
                f"Failed to render command-card QR label: {exc}"
            ) from exc
        output_path = self._resolve_command_label_output_path(
            options.get("label_output")
        )
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            image.save(output_path, format="PNG")
        except OSError as exc:
            raise CommandError(
                f"Failed to write command-card label preview '{output_path}': {exc}"
            ) from exc

        label_result = {
            "printed": False,
            "printer": options["label_printer"],
            "preview": str(output_path),
            "payload": payload,
            "template": template.name,
            "card_label": card_label,
            "dry_run": bool(options.get("label_dry_run")),
        }
        if options["label_printer"] == "none":
            return label_result
        if options["label_printer"] != "phomemo-m220":
            raise CommandError(f"Unsupported label printer: {options['label_printer']}")
        try:
            job = build_phomemo_m220_job(
                image,
                speed=options["speed"],
                density=options["density"],
            )
        except Exception as exc:
            raise CommandError(
                f"Failed to build command-card M220 label job: {exc}"
            ) from exc
        label_result["command_bytes"] = len(job)
        if options.get("label_dry_run"):
            return label_result

        try:
            usb_path = resolve_phomemo_m220_usb_path(options.get("usb_path"))
        except Exception as exc:
            raise CommandError(
                f"Failed to resolve Phomemo M220 USB path: {exc}"
            ) from exc
        if not usb_path:
            message = (
                "No Phomemo M220 USB path configured. Pass --usb-path, set "
                f"{PHOMEMO_M220_USB_PATH_ENV}, or run `python manage.py printers devices`."
            )
            if raise_errors:
                raise CommandError(message)
            label_result["error"] = message
            return label_result
        try:
            written = write_windows_usb(
                usb_path,
                job,
                chunk_size=options["chunk_bytes"],
                delay_seconds=options["chunk_delay"],
            )
        except Exception as exc:
            raise CommandError(
                f"Failed to write command-card M220 label: {exc}"
            ) from exc
        label_result["printed"] = True
        label_result["written_bytes"] = written
        return label_result

    def _command_template_tracking_url(
        self,
        template: RFIDCommandTemplate,
        *,
        base_url: str | None,
    ) -> str:
        base = (base_url or getattr(settings, "PUBLIC_BASE_URL", "") or "").strip()
        return template.get_qr_target_url(base)

    def _command_card_sticker_card_label(self, tag: RFID) -> str:
        tag.ensure_generated_label()
        return (
            (tag.custom_label or "").strip()
            or (tag.generated_label or "").strip()
            or (tag.name_key or "").strip()
            or str(tag.pk)
        )

    def _resolve_command_label_output_path(self, output: str | None) -> Path:
        if output:
            return Path(output).expanduser().resolve()
        fd, path = tempfile.mkstemp(
            prefix="arthexis-command-card-label-",
            suffix=".png",
        )
        os.close(fd)
        return Path(path)

    def _add_sync_arguments(self, parser):
        subparsers = parser.add_subparsers(dest="sync_action", required=True)

        export_parser = subparsers.add_parser(
            "export", help="Write an RFID node-transfer JSON bundle."
        )
        export_parser.add_argument(
            "path", nargs="?", help="File to write JSON to; stdout if omitted."
        )
        export_parser.add_argument(
            "--authorized-only",
            action="store_true",
            help="Export only RFIDs that are currently allowed.",
        )
        export_parser.add_argument(
            "--compact",
            action="store_true",
            help="Write compact JSON instead of pretty-printed JSON.",
        )

        import_parser = subparsers.add_parser(
            "import", help="Apply an RFID node-transfer JSON bundle."
        )
        import_parser.add_argument("path", help="JSON bundle to import.")
        import_parser.add_argument(
            "--origin-node",
            help=(
                "Optional Node UUID, hostname, or MAC address to record as the "
                "source for imported RFIDs."
            ),
        )

    def _handle_sync(self, options):
        sync_action = options.get("sync_action")
        if sync_action == "export":
            self._handle_sync_export(options)
            return
        if sync_action == "import":
            self._handle_sync_import(options)
            return
        raise CommandError(f"Unsupported RFID sync action: {sync_action}")

    def _handle_sync_export(self, options):
        path = options.get("path")
        tags = RFID.objects.all()
        if options.get("authorized_only"):
            tags = tags.filter(allowed=True)
        tags = tags.order_by("rfid").prefetch_related("energy_accounts")
        rfids = [serialize_rfid(tag) for tag in tags]
        payload = {
            "format": self.SYNC_FORMAT,
            "version": self.SYNC_VERSION,
            "source_node": self._local_node_payload(),
            "rfids": rfids,
        }
        dump_kwargs = (
            {"separators": (",", ":")}
            if options.get("compact")
            else {"indent": 2, "sort_keys": True}
        )
        output = json.dumps(payload, ensure_ascii=False, **dump_kwargs)
        if path:
            Path(path).write_text(output + "\n", encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"Exported {len(rfids)} RFID tags"))
            return
        self.stdout.write(output)
        self.stderr.write(self.style.SUCCESS(f"Exported {len(rfids)} RFID tags"))

    def _handle_sync_import(self, options):
        path = Path(options["path"])
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise CommandError(str(exc)) from exc
        except json.JSONDecodeError as exc:
            raise CommandError(f"Invalid JSON bundle: {exc}") from exc

        rfids = self._rfids_from_sync_payload(payload)
        origin_node = self._resolve_sync_origin_node(
            options.get("origin_node"), payload
        )

        created = 0
        updated = 0
        linked_accounts = 0
        missing_accounts: list[str] = []
        errors = 0
        for entry in rfids:
            if not isinstance(entry, dict):
                errors += 1
                continue
            outcome = apply_rfid_payload(entry, origin_node=origin_node)
            if not outcome.ok:
                errors += 1
                if outcome.error:
                    missing_accounts.append(outcome.error)
                continue
            if outcome.created:
                created += 1
            else:
                updated += 1
            linked_accounts += outcome.accounts_linked
            missing_accounts.extend(outcome.missing_accounts)

        summary = (
            f"Imported {len(rfids)} RFID tags: {created} created, "
            f"{updated} updated, {linked_accounts} account links"
        )
        if errors:
            summary = f"{summary}, {errors} errors"
        if missing_accounts:
            summary = f"{summary}, missing accounts: {', '.join(missing_accounts)}"
        self.stdout.write(self.style.SUCCESS(summary))

    def _rfids_from_sync_payload(self, payload):
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            raise CommandError("RFID sync bundle must be a JSON object or list")
        if payload.get("format") not in (None, self.SYNC_FORMAT):
            raise CommandError(f"Unsupported RFID sync format: {payload.get('format')}")
        version = payload.get("version", self.SYNC_VERSION)
        if version != self.SYNC_VERSION:
            raise CommandError(f"Unsupported RFID sync version: {version}")
        rfids = payload.get("rfids")
        if not isinstance(rfids, list):
            raise CommandError("RFID sync bundle must contain an rfids list")
        return rfids

    def _local_node_payload(self):
        node = Node.get_local()
        if node is None:
            return {}
        return {
            "uuid": str(node.uuid),
            "hostname": node.hostname,
            "mac_address": node.mac_address,
        }

    def _resolve_sync_origin_node(self, explicit_origin, payload):
        hints: list[str] = []
        if explicit_origin:
            hints.append(str(explicit_origin).strip())
        elif isinstance(payload, dict) and isinstance(payload.get("source_node"), dict):
            source_node = payload["source_node"]
            hints.extend(
                str(source_node.get(key) or "").strip()
                for key in ("uuid", "hostname", "mac_address")
            )
        for hint in [value for value in hints if value]:
            node = None
            try:
                node = Node.objects.filter(uuid=UUID(hint)).first()
            except ValueError:
                node = None
            node = (
                node
                or Node.objects.filter(hostname=hint).first()
                or Node.objects.filter(mac_address=hint).first()
            )
            if node is not None:
                return node
        return None

    def _handle_doctor(self, options):
        timeout = options["timeout"]
        scan_requested = options["scan"]
        deep_read_requested = options["deep_read"]
        no_input = options["no_input"]
        show_raw = options["show_raw"]
        self.stdout.write(self.style.MIGRATE_HEADING("RFID Doctor"))
        endpoint = rfid_service.service_endpoint()
        self.stdout.write(
            f"Service endpoint: {endpoint.host}:{endpoint.port} (RFID_SERVICE_HOST/PORT)"
        )
        service_lock = rfid_service.rfid_service_lock_path()
        scanner_lock = rfid_scan_lock_path()
        self.stdout.write(
            f"Service lock: {service_lock} ({'present' if service_lock.exists() else 'missing'})"
        )
        self.stdout.write(
            f"Scanner lock: {scanner_lock} ({'present' if scanner_lock.exists() else 'missing'})"
        )
        configured = rfid_service_enabled()
        self.stdout.write(
            f"RFID reader configuration: {'configured' if configured else 'not configured'}"
        )
        self._report_device_status(configured)
        ping = rfid_service.request_service("ping", timeout=0.5)
        if ping:
            payload = ping if show_raw else rfid_service.sanitize_rfid_payload(ping)
            self.stdout.write(self.style.SUCCESS("RFID service responded to ping."))
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
        else:
            self.stdout.write(
                self.style.WARNING(
                    "RFID service did not respond to ping. Check the systemd unit and service endpoint configuration."
                )
            )
        if deep_read_requested:
            response = rfid_service.deep_read_via_service()
            if response is None:
                self.stdout.write(
                    self.style.WARNING(
                        "RFID service did not respond to deep-read toggle."
                    )
                )
            else:
                payload = (
                    response
                    if show_raw
                    else rfid_service.sanitize_rfid_payload(response)
                )
                self.stdout.write(self.style.SUCCESS("Deep-read toggle response:"))
                self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
        should_scan = scan_requested
        if not scan_requested and not no_input and sys.stdin.isatty():
            should_scan = self._prompt_yes_no(
                "Attempt a scan via the RFID service now?", default=False
            )
        if should_scan:
            self._run_doctor_scan(timeout, show_raw=show_raw)

    def _prompt_yes_no(self, question, default=False):
        prompt = "[Y/n]" if default else "[y/N]"
        while True:
            answer = input(f"{question} {prompt} ").strip().lower()
            if not answer:
                return default
            if answer in {"y", "yes"}:
                return True
            if answer in {"n", "no"}:
                return False

    def _run_doctor_scan(self, timeout, *, show_raw=False):
        self.stdout.write(
            "Hold an RFID card near the reader, then wait for the scan result..."
        )
        payload = self._scan_via_attempt(timeout)
        if payload.get("error") or not payload.get("rfid"):
            self.stdout.write(
                self.style.WARNING("No new RFID scan recorded before timeout.")
            )
            return
        payload.setdefault("attempted_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
        if not show_raw:
            payload = rfid_service.sanitize_rfid_payload(payload)
        self.stdout.write(self.style.SUCCESS("Scan response:"))
        self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))

    def _report_device_status(self, configured: bool) -> None:
        detection = detect_scanner()
        if detection.get("detected"):
            self.stdout.write(self.style.SUCCESS("RFID device status: detected"))
        else:
            reason = detection.get("reason") or "unknown"
            self.stdout.write(
                self.style.WARNING(
                    f"RFID device status: not detected (reason: {reason})"
                )
            )
        lockfile = detection.get("lockfile")
        if lockfile:
            self.stdout.write(f"Scanner lockfile: {lockfile}")
        if configured and detection.get("detected"):
            return
        self.stdout.write(self.style.MIGRATE_HEADING("Troubleshooting checklist"))
        if not configured:
            self.stdout.write(
                "- Ensure the RFID reader lock file exists (./.locks/rfid-service.lck) or enable auto-detect."
            )
        if not detection.get("detected"):
            self.stdout.write("- Confirm SPI is enabled and /dev/spidev* is present.")
            self.stdout.write(
                "- Verify the MFRC522 and GPIO libraries are installed and accessible."
            )
            self.stdout.write("- Check wiring (3.3V, GND, SDA, SCK, MOSI, MISO, IRQ).")
        self.stdout.write(
            "- Start the RFID service in debug mode to collect logs: ./command.sh rfid service --debug"
        )
        self.stdout.write(
            f"- Review RFID logs in {settings.LOG_DIR}/rfid.log for detailed errors."
        )

    def _add_import_arguments(self, parser):
        parser.add_argument("path", help="CSV file to import")
        parser.add_argument(
            "--color",
            choices=[c[0] for c in RFID.COLOR_CHOICES] + ["ALL"],
            default="ALL",
            help="Import only RFIDs with this color code (default: ALL)",
        )
        parser.add_argument(
            "--released",
            choices=["true", "false", "all"],
            default="all",
            help="Import only RFIDs with this released state (default: all)",
        )
        parser.add_argument(
            "--account-field",
            choices=["id", "name"],
            default="id",
            help="Read customer accounts from id or name fields.",
        )

    def _handle_import(self, options):
        path = options["path"]
        color_filter = options["color"].upper()
        released_filter = options["released"]
        account_field = options["account_field"]
        accounts_column = account_column_for_field(account_field)
        try:
            with open(path, newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                count = 0
                for row in reader:
                    rfid_value = row.get("rfid", "").strip()
                    energy_accounts = row.get(accounts_column, "")
                    custom_label = row.get("custom_label", "").strip()
                    allowed = row.get("allowed", "True").strip().lower() != "false"
                    color = row.get("color", RFID.BLACK).strip().upper() or RFID.BLACK
                    released = row.get("released", "False").strip().lower() == "true"
                    if not rfid_value:
                        continue
                    if color_filter != "ALL" and color != color_filter:
                        continue
                    if released_filter != "all" and released != (
                        released_filter == "true"
                    ):
                        continue
                    tag, _ = RFID.update_or_create_from_code(
                        rfid_value,
                        {
                            "custom_label": custom_label,
                            "allowed": allowed,
                            "color": color,
                            "released": released,
                        },
                    )
                    row_context = {
                        accounts_column: energy_accounts,
                        "customer_accounts": row.get("customer_accounts", ""),
                        "customer_account_names": row.get("customer_account_names", ""),
                        "energy_accounts": row.get("energy_accounts", ""),
                        "energy_account_names": row.get("energy_account_names", ""),
                    }
                    accounts = parse_accounts(row_context, account_field)
                    if accounts:
                        tag.energy_accounts.set(accounts)
                    else:
                        tag.energy_accounts.clear()
                    count += 1
        except FileNotFoundError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Imported {count} tags"))

    def _add_export_arguments(self, parser):
        parser.add_argument(
            "path", nargs="?", help="File to write CSV to; stdout if omitted"
        )
        parser.add_argument(
            "--color",
            choices=[c[0] for c in RFID.COLOR_CHOICES] + ["ALL"],
            default=RFID.BLACK,
            help=f"Filter RFIDs by color code (default: {RFID.BLACK})",
        )
        parser.add_argument(
            "--released",
            choices=["true", "false", "all"],
            default="all",
            help="Filter RFIDs by released state (default: all)",
        )
        parser.add_argument(
            "--account-field",
            choices=["id", "name"],
            default="id",
            help="Include customer accounts using the selected field.",
        )

    def _handle_export(self, options):
        path = options["path"]
        color = options["color"].upper()
        released = options["released"]
        account_field = options["account_field"]
        qs = RFID.objects.all()
        if color != "ALL":
            qs = qs.filter(color=color)
        if released != "all":
            qs = qs.filter(released=(released == "true"))
        qs = qs.order_by("rfid")
        accounts_column = account_column_for_field(account_field)

        rows = (
            (
                t.rfid,
                t.custom_label,
                serialize_accounts(t, account_field),
                str(t.allowed),
                t.color,
                str(t.released),
            )
            for t in qs
        )
        exported_count = 0
        if path:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(
                    [
                        "rfid",
                        "custom_label",
                        accounts_column,
                        "allowed",
                        "color",
                        "released",
                    ]
                )
                for row in rows:
                    writer.writerow(row)
                    exported_count += 1
        else:
            writer = csv.writer(self.stdout)
            writer.writerow(
                [
                    "rfid",
                    "custom_label",
                    accounts_column,
                    "allowed",
                    "color",
                    "released",
                ]
            )
            for row in rows:
                writer.writerow(row)
                exported_count += 1
        self.stdout.write(self.style.SUCCESS(f"Exported {exported_count} tags"))
