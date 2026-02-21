from __future__ import annotations

import argparse
import csv
import json
import logging
import subprocess
import sys
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from apps.cards import rfid_service
from apps.cards.background_reader import is_configured, lock_file_path
from apps.cards.detect import detect_scanner
from apps.cards.models import RFID, RFIDAttempt
from apps.cards.reader import validate_rfid_value
from apps.cards.rfid_import_export import account_column_for_field, parse_accounts, serialize_accounts
from apps.cards.rfid_service import rfid_service_enabled, run_service, service_available, service_endpoint
from apps.cards.scanner import scan_sources
from apps.cards.utils import drain_stdin, user_requested_stop
from apps.loggers.handlers import RFIDFileHandler


class Command(BaseCommand):
    """Canonical command group for RFID operations."""

    help = "RFID command group. Use `rfid <check|watch|service|doctor|import|export>`."
    DEFAULT_SCAN_TIMEOUT = max(30.0, rfid_service.DEFAULT_SCAN_TIMEOUT)

    def add_arguments(self, parser):
        """Register subcommands and their arguments."""
        subparsers = parser.add_subparsers(dest="action", required=True)

        check_parser = subparsers.add_parser("check", help="Validate RFID tags by UID, label, or scan.")
        self._add_check_arguments(check_parser)

        watch_parser = subparsers.add_parser("watch", help="Toggle the always-on RFID watcher.")
        watch_parser.add_argument("--stop", action="store_true", help="Stop the always-on watcher instead of starting it")

        service_parser = subparsers.add_parser("service", help="Run the RFID scanner UDP service.")
        self._add_service_arguments(service_parser)

        doctor_parser = subparsers.add_parser("doctor", help="Run RFID diagnostics.")
        self._add_doctor_arguments(doctor_parser)

        import_parser = subparsers.add_parser("import", help="Import RFIDs from CSV.")
        self._add_import_arguments(import_parser)

        export_parser = subparsers.add_parser("export", help="Export RFIDs to CSV.")
        self._add_export_arguments(export_parser)

    def handle(self, *args, **options):
        """Dispatch to the selected RFID action."""
        action = options["action"]
        handler = getattr(self, f"_handle_{action}", None)
        if handler is None:
            raise CommandError(f"Unsupported RFID action: {action}")
        handler(options)

    def _add_check_arguments(self, parser):
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--label", help="Validate an RFID associated with the given label id or custom label.")
        target.add_argument("--uid", help="Validate an RFID by providing the UID value directly.")
        target.add_argument("--scan", action="store_true", help="Start the RFID scanner and return the first successfully read tag.")
        parser.add_argument("--kind", choices=[choice[0] for choice in RFID.KIND_CHOICES], help="Optional RFID kind when validating a UID directly.")
        parser.add_argument("--endianness", choices=[choice[0] for choice in RFID.ENDIANNESS_CHOICES], help="Optional endianness when validating a UID directly.")
        parser.add_argument("--timeout", type=float, default=5.0, help="How long to wait for a scan before timing out when running non-interactively (seconds).")
        parser.add_argument("--pretty", action="store_true", help="Pretty-print the JSON response.")

    def _handle_check(self, options):
        if options.get("scan"):
            result = self._scan(options)
        elif options.get("label"):
            result = self._validate_label(options["label"])
        else:
            result = self._validate_uid(options.get("uid"), kind=options.get("kind"), endianness=options.get("endianness"))

        if "error" in result:
            raise CommandError(result["error"])

        dump_kwargs = {"indent": 2, "sort_keys": True} if options.get("pretty", False) else {}
        self.stdout.write(json.dumps(result, **dump_kwargs))

    def _validate_uid(self, value: str | None, *, kind: str | None, endianness: str | None):
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

        result = self._scan_via_attempt(timeout) if service_available() else self._scan_via_local(timeout)
        if result.get("error"):
            return result
        if not result.get("rfid"):
            if not is_configured() and not service_available():
                return {"error": "RFID scanner not configured or detected"}
            return {"error": "No RFID detected before timeout"}
        return result

    def _scan_via_attempt(self, timeout: float) -> dict:
        interactive = sys.stdin.isatty()
        if interactive:
            self.stdout.write("Press any key to stop scanning.")
            drain_stdin()
        self.stdout.flush()
        start = time.monotonic()
        latest_id = RFIDAttempt.objects.filter(source=RFIDAttempt.Source.SERVICE).order_by("-pk").values_list("pk", flat=True).first()
        attempt = None
        while True:
            if interactive and user_requested_stop():
                return {"error": "Scan cancelled by user"}
            attempt = RFIDAttempt.objects.filter(source=RFIDAttempt.Source.SERVICE, pk__gt=latest_id or 0).order_by("pk").first()
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

    def _scan_via_local(self, timeout: float) -> dict:
        interactive = sys.stdin.isatty()
        if interactive:
            self.stdout.write("Press any key to stop scanning.")
            drain_stdin()
        self.stdout.flush()
        start = time.monotonic()
        while True:
            if interactive and user_requested_stop():
                return {"error": "Scan cancelled by user"}
            result = scan_sources(timeout=0.2)
            if result.get("rfid") or result.get("error"):
                return result
            if not interactive and time.monotonic() - start >= timeout:
                return {"rfid": None, "label_id": None}
        return {"rfid": None, "label_id": None}

    def _handle_watch(self, options):
        from apps.cards.always_on import is_running, start, stop

        if options["stop"]:
            stop()
            self.stdout.write(self.style.SUCCESS("RFID watch disabled"))
            return
        start()
        state = "enabled" if is_running() else "disabled"
        self.stdout.write(self.style.SUCCESS(f"RFID watch {state}"))

    def _add_service_arguments(self, parser):
        endpoint = service_endpoint()
        parser.add_argument("--host", default=endpoint.host, help="Host interface to bind the RFID service")
        parser.add_argument("--port", type=int, default=endpoint.port, help="UDP port to bind the RFID service")
        parser.add_argument("--debug", action=argparse.BooleanOptionalAction, default=False, help="Enable or disable debug logging for interactive troubleshooting")

    def _handle_service(self, options):
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
        handler = RFIDFileHandler(filename="rfid.log", when="midnight", backupCount=3, encoding="utf-8")
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        logger.addHandler(handler)

    def _prepare_debug_service(self) -> None:
        base_dir = Path(settings.BASE_DIR)
        lock_dir = base_dir / ".locks"
        feature_enabled = rfid_service_enabled(lock_dir)
        service_name = self._resolve_service_name(lock_dir)
        if not service_name:
            if feature_enabled:
                self.stdout.write(self.style.WARNING("RFID service feature is enabled, but .locks/service.lck is missing; unable to stop the systemd service before debug start."))
            return
        unit_name = f"rfid-{service_name}.service"
        active = self._systemd_is_active(unit_name)
        if active:
            self._stop_systemd_unit(unit_name)
            return
        if feature_enabled:
            self.stdout.write(self.style.WARNING(f"RFID service feature is enabled, but {unit_name} is not active; starting a debug instance."))

    def _resolve_service_name(self, lock_dir: Path) -> str | None:
        service_file = lock_dir / "service.lck"
        if not service_file.is_file():
            return None
        service_name = service_file.read_text(encoding="utf-8").strip()
        return service_name or None

    def _systemd_is_active(self, unit_name: str) -> bool | None:
        try:
            result = subprocess.run(["systemctl", "is-active", unit_name], capture_output=True, text=True, check=False)
        except FileNotFoundError:
            self.stdout.write(self.style.WARNING(f"systemctl not available; cannot verify {unit_name} status before debug start."))
            return None
        status = result.stdout.strip()
        return result.returncode == 0 and status in {"active", "activating", "reloading"}

    def _stop_systemd_unit(self, unit_name: str) -> None:
        self.stdout.write(f"Stopping {unit_name} to start debug service...")
        try:
            result = subprocess.run(["systemctl", "stop", unit_name], capture_output=True, text=True, check=False)
        except FileNotFoundError:
            self.stdout.write(self.style.WARNING(f"systemctl not available; cannot stop {unit_name} before debug start."))
            return
        if result.returncode != 0:
            self.stdout.write(self.style.WARNING(f"Failed to stop {unit_name} before debug start: {(result.stderr or '').strip()}"))
            return
        self.stdout.write(self.style.SUCCESS(f"Stopped {unit_name}"))

    def _add_doctor_arguments(self, parser):
        parser.add_argument("--timeout", type=float, default=self.DEFAULT_SCAN_TIMEOUT, help="Scan timeout in seconds when running non-interactively (default: %(default)s)")
        parser.add_argument("--scan", action="store_true", help="Attempt a scan via the RFID service after checks.")
        parser.add_argument("--deep-read", action="store_true", help="Toggle deep-read mode via the RFID service.")
        parser.add_argument("--no-input", action="store_true", help="Skip interactive prompts.")
        parser.add_argument("--show-raw", action="store_true", help="Show raw RFID values in output (default is masked).")

    def _handle_doctor(self, options):
        timeout = options["timeout"]
        scan_requested = options["scan"]
        deep_read_requested = options["deep_read"]
        no_input = options["no_input"]
        show_raw = options["show_raw"]
        self.stdout.write(self.style.MIGRATE_HEADING("RFID Doctor"))
        endpoint = rfid_service.service_endpoint()
        self.stdout.write(f"Service endpoint: {endpoint.host}:{endpoint.port} (RFID_SERVICE_HOST/PORT)")
        service_lock = rfid_service.rfid_service_lock_path()
        scanner_lock = lock_file_path()
        self.stdout.write(f"Service lock: {service_lock} ({'present' if service_lock.exists() else 'missing'})")
        self.stdout.write(f"Scanner lock: {scanner_lock} ({'present' if scanner_lock.exists() else 'missing'})")
        configured = is_configured()
        self.stdout.write(f"RFID reader configuration: {'configured' if configured else 'not configured'}")
        self._report_device_status(configured)
        ping = rfid_service.request_service("ping", timeout=0.5)
        if ping:
            payload = ping if show_raw else rfid_service.sanitize_rfid_payload(ping)
            self.stdout.write(self.style.SUCCESS("RFID service responded to ping."))
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
        else:
            self.stdout.write(self.style.WARNING("RFID service did not respond to ping. Check the systemd unit and service endpoint configuration."))
        if deep_read_requested:
            response = rfid_service.deep_read_via_service()
            if response is None:
                self.stdout.write(self.style.WARNING("RFID service did not respond to deep-read toggle."))
            else:
                payload = response if show_raw else rfid_service.sanitize_rfid_payload(response)
                self.stdout.write(self.style.SUCCESS("Deep-read toggle response:"))
                self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
        should_scan = scan_requested
        if not scan_requested and not no_input and sys.stdin.isatty():
            should_scan = self._prompt_yes_no("Attempt a scan via the RFID service now?", default=False)
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
        self.stdout.write("Hold an RFID card near the reader, then wait for the scan result...")
        payload = self._scan_via_attempt(timeout)
        if payload.get("error") or not payload.get("rfid"):
            self.stdout.write(self.style.WARNING("No new RFID scan recorded before timeout."))
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
            self.stdout.write(self.style.WARNING(f"RFID device status: not detected (reason: {reason})"))
        lockfile = detection.get("lockfile")
        if lockfile:
            self.stdout.write(f"Scanner lockfile: {lockfile}")
        if configured and detection.get("detected"):
            return
        self.stdout.write(self.style.MIGRATE_HEADING("Troubleshooting checklist"))
        if not configured:
            self.stdout.write("- Ensure the RFID reader lock file exists (./.locks/rfid.lck) or enable auto-detect.")
        if not detection.get("detected"):
            self.stdout.write("- Confirm SPI is enabled and /dev/spidev* is present.")
            self.stdout.write("- Verify the MFRC522 and GPIO libraries are installed and accessible.")
            self.stdout.write("- Check wiring (3.3V, GND, SDA, SCK, MOSI, MISO, IRQ).")
        self.stdout.write("- Start the RFID service in debug mode to collect logs: ./command.sh rfid service --debug")
        self.stdout.write(f"- Review RFID logs in {settings.LOG_DIR}/rfid.log for detailed errors.")

    def _add_import_arguments(self, parser):
        parser.add_argument("path", help="CSV file to import")
        parser.add_argument("--color", choices=[c[0] for c in RFID.COLOR_CHOICES] + ["ALL"], default="ALL", help="Import only RFIDs with this color code (default: ALL)")
        parser.add_argument("--released", choices=["true", "false", "all"], default="all", help="Import only RFIDs with this released state (default: all)")
        parser.add_argument("--account-field", choices=["id", "name"], default="id", help="Read customer accounts from id or name fields.")

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
                    if released_filter != "all" and released != (released_filter == "true"):
                        continue
                    tag, _ = RFID.update_or_create_from_code(rfid_value, {"custom_label": custom_label, "allowed": allowed, "color": color, "released": released})
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
        parser.add_argument("path", nargs="?", help="File to write CSV to; stdout if omitted")
        parser.add_argument("--color", choices=[c[0] for c in RFID.COLOR_CHOICES] + ["ALL"], default=RFID.BLACK, help=f"Filter RFIDs by color code (default: {RFID.BLACK})")
        parser.add_argument("--released", choices=["true", "false", "all"], default="all", help="Filter RFIDs by released state (default: all)")
        parser.add_argument("--account-field", choices=["id", "name"], default="id", help="Include customer accounts using the selected field.")

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

        rows = ((t.rfid, t.custom_label, serialize_accounts(t, account_field), str(t.allowed), t.color, str(t.released)) for t in qs)
        exported_count = 0
        if path:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(["rfid", "custom_label", accounts_column, "allowed", "color", "released"])
                for row in rows:
                    writer.writerow(row)
                    exported_count += 1
        else:
            writer = csv.writer(self.stdout)
            writer.writerow(["rfid", "custom_label", accounts_column, "allowed", "color", "released"])
            for row in rows:
                writer.writerow(row)
                exported_count += 1
        self.stdout.write(self.style.SUCCESS(f"Exported {exported_count} tags"))
