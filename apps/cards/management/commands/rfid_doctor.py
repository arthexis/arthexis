import json
import sys

from django.core.management.base import BaseCommand

from apps.cards import rfid_service
from apps.cards.background_reader import is_configured, lock_file_path


class Command(BaseCommand):
    help = (
        "Interactively verify RFID service availability, configuration, and scans."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--timeout",
            type=float,
            default=rfid_service.DEFAULT_SCAN_TIMEOUT,
            help="Scan timeout in seconds (default: %(default)s)",
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
        scan_lock = rfid_service.rfid_scan_lock_path()
        self._print_lock_status("Service lock", service_lock)
        self._print_lock_status("Scanner lock", scanner_lock)
        self._print_lock_status("Last scan lock", scan_lock)

        configured = is_configured()
        config_status = "configured" if configured else "not configured"
        self.stdout.write(f"RFID reader configuration: {config_status}")

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
        self.stdout.write(
            "Hold an RFID card near the reader, then wait for the scan result..."
        )
        response = rfid_service.scan_via_service(timeout=timeout)
        if response is None:
            self.stdout.write(
                self.style.WARNING(
                    "RFID service did not respond to scan request."
                )
            )
            return
        payload = self._format_payload(response, show_raw=show_raw)
        self.stdout.write(self.style.SUCCESS("Scan response:"))
        self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
