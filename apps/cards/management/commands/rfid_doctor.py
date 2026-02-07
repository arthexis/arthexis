import json
import sys
import time

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.cards import rfid_service
from apps.cards.background_reader import is_configured, lock_file_path
from apps.cards.detect import detect_scanner
from apps.cards.models import RFIDAttempt
from apps.cards.utils import drain_stdin, user_requested_stop


class Command(BaseCommand):
    help = (
        "Interactively verify RFID service availability, configuration, and scans."
    )

    DEFAULT_SCAN_TIMEOUT = max(30.0, rfid_service.DEFAULT_SCAN_TIMEOUT)

    def add_arguments(self, parser):
        parser.add_argument(
            "--timeout",
            type=float,
            default=self.DEFAULT_SCAN_TIMEOUT,
            help=(
                "Scan timeout in seconds when running non-interactively "
                "(default: %(default)s)"
            ),
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
            "--no-input",
            action="store_true",
            help="Skip interactive prompts.",
        )
        parser.add_argument(
            "--show-raw",
            action="store_true",
            help="Show raw RFID values in output (default is masked).",
        )

    def handle(self, *args, **options):
        timeout = options["timeout"]
        scan_requested = options["scan"]
        deep_read_requested = options["deep_read"]
        no_input = options["no_input"]
        show_raw = options["show_raw"]

        self.stdout.write(self.style.MIGRATE_HEADING("RFID Doctor"))
        endpoint = rfid_service.service_endpoint()
        self.stdout.write(
            f"Service endpoint: {endpoint.host}:{endpoint.port} "
            f"(RFID_SERVICE_HOST/PORT)",
        )

        service_lock = rfid_service.rfid_service_lock_path()
        scanner_lock = lock_file_path()
        self._print_lock_status("Service lock", service_lock)
        self._print_lock_status("Scanner lock", scanner_lock)

        configured = is_configured()
        config_status = "configured" if configured else "not configured"
        self.stdout.write(f"RFID reader configuration: {config_status}")
        self._report_device_status(configured)

        ping = rfid_service.request_service("ping", timeout=0.5)
        if ping:
            payload = self._format_payload(ping, show_raw=show_raw)
            self.stdout.write(self.style.SUCCESS("RFID service responded to ping."))
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
        else:
            self.stdout.write(
                self.style.WARNING(
                    "RFID service did not respond to ping. Check the systemd unit "
                    "and service endpoint configuration."
                )
            )

        if deep_read_requested:
            self._toggle_deep_read(show_raw=show_raw)

        should_scan = scan_requested
        if not scan_requested and not no_input and sys.stdin.isatty():
            should_scan = self._prompt_yes_no(
                "Attempt a scan via the RFID service now?",
                default=False,
            )

        if should_scan:
            self._run_scan(timeout, show_raw=show_raw)

    def _print_lock_status(self, label, path):
        status = "present" if path.exists() else "missing"
        self.stdout.write(f"{label}: {path} ({status})")

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

    def _format_payload(self, payload, *, show_raw=False):
        if show_raw:
            return payload
        return rfid_service.sanitize_rfid_payload(payload)

    def _toggle_deep_read(self, *, show_raw=False):
        response = rfid_service.deep_read_via_service()
        if response is None:
            self.stdout.write(
                self.style.WARNING("RFID service did not respond to deep-read toggle."),
            )
            return
        payload = self._format_payload(response, show_raw=show_raw)
        self.stdout.write(self.style.SUCCESS("Deep-read toggle response:"))
        self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))

    def _run_scan(self, timeout, *, show_raw=False):
        interactive = sys.stdin.isatty()
        self.stdout.write(
            "Hold an RFID card near the reader, then wait for the scan result..."
        )
        if interactive:
            self.stdout.write("Press any key to stop scanning.")
        self.stdout.flush()
        if interactive:
            drain_stdin()
        start = time.monotonic()
        latest_id = (
            RFIDAttempt.objects.filter(source=RFIDAttempt.Source.SERVICE)
            .order_by("-pk")
            .values_list("pk", flat=True)
            .first()
        )
        attempt = None
        while True:
            if interactive and user_requested_stop():
                self.stdout.write(self.style.WARNING("Scan cancelled by user."))
                return
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
            self.stdout.write(
                self.style.WARNING(
                    "No new RFID scan recorded before timeout."
                )
            )
            return
        payload = dict(attempt.payload or {})
        payload.setdefault("rfid", attempt.rfid)
        if attempt.label_id:
            payload.setdefault("label_id", attempt.label_id)
        payload.setdefault("attempted_at", attempt.attempted_at.isoformat())
        payload = self._format_payload(payload, show_raw=show_raw)
        self.stdout.write(self.style.SUCCESS("Scan response:"))
        self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))

    def _report_device_status(self, configured: bool) -> None:
        detection = detect_scanner()
        if detection.get("detected"):
            irq_pin = detection.get("irq_pin")
            assumed = detection.get("assumed")
            status = "detected" if not assumed else "assumed active"
            details = []
            if irq_pin is not None:
                details.append(f"IRQ pin {irq_pin}")
            reason = detection.get("reason")
            if reason:
                details.append(f"reason: {reason}")
            suffix = f" ({', '.join(details)})" if details else ""
            if assumed:
                self.stdout.write(
                    self.style.WARNING(f"RFID device status: {status}{suffix}")
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(f"RFID device status: {status}{suffix}")
                )
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
        hints = []
        if not configured:
            hints.append(
                "Ensure the RFID reader lock file exists (./.locks/rfid.lck) or enable auto-detect."
            )
        if not detection.get("detected"):
            hints.extend(
                [
                    "Confirm SPI is enabled and /dev/spidev* is present.",
                    "Verify the MFRC522 and GPIO libraries are installed and accessible.",
                    "Check wiring (3.3V, GND, SDA, SCK, MOSI, MISO, IRQ).",
                ]
            )
        hints.append(
            "Start the RFID service in debug mode to collect logs: ./command.sh rfid-service --debug"
        )
        hints.append(
            f"Review RFID logs in {settings.LOG_DIR}/rfid.log for detailed errors."
        )
        for hint in hints:
            self.stdout.write(f"- {hint}")
