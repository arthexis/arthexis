"""Label printer command group."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

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
    iter_phomemo_m220_usb_paths,
    resolve_phomemo_m220_usb_path,
    write_windows_usb,
)


class Command(BaseCommand):
    help = "Printers command group."

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(dest="action")
        subparsers.required = True

        devices_parser = subparsers.add_parser("devices", help="List auto-discovered printer device paths.")
        devices_parser.add_argument("--printer", choices=["phomemo-m220"], default="phomemo-m220")

        test_parser = subparsers.add_parser("test", help="Build a sample print job.")
        self._add_common_print_arguments(test_parser)
        test_parser.add_argument("--printer", choices=["phomemo-m220"], default="phomemo-m220")
        test_parser.set_defaults(dry_run=True, text="PRINTER TEST")

        print_parser = subparsers.add_parser("print-label", help="Render a text label and optionally print it.")
        self._add_common_print_arguments(print_parser)
        print_parser.add_argument("--text", required=True)
        print_parser.add_argument("--printer", choices=["none", "phomemo-m220"], default="none")
        print_parser.add_argument("--dry-run", action="store_true")

    def _add_common_print_arguments(self, parser):
        parser.add_argument("--label-title", default="QR CODE")
        parser.add_argument("--label-subtitle", default="")
        parser.add_argument("--footer", default="")
        parser.add_argument("--width", type=int, default=DEFAULT_LABEL_WIDTH)
        parser.add_argument("--height", type=int, default=DEFAULT_LABEL_HEIGHT)
        parser.add_argument("--qr-size", type=int, default=DEFAULT_QR_SIZE)
        parser.add_argument("--chunk-bytes", type=int, default=DEFAULT_CHUNK_BYTES)
        parser.add_argument("--chunk-delay", type=float, default=DEFAULT_CHUNK_DELAY_SECONDS)
        parser.add_argument("--speed", type=int, default=2)
        parser.add_argument("--density", type=int, default=15)
        parser.add_argument("--usb-path")

    def handle(self, *args, **options):
        action = options["action"]
        if action == "devices":
            self._handle_devices(options)
            return
        if action in {"test", "print-label"}:
            self._handle_print(options)
            return
        raise CommandError(f"Unsupported action: {action}")

    def _handle_devices(self, options):
        if options["printer"] != "phomemo-m220":
            raise CommandError(f"Unsupported printer: {options['printer']}")
        env_path = resolve_phomemo_m220_usb_path("")
        if env_path:
            self.stdout.write(f"CONFIGURED_OR_DISCOVERED={env_path}")
        paths = list(iter_phomemo_m220_usb_paths())
        if not paths:
            self.stdout.write("No Phomemo M220 USBPRINT candidates found.")
            return
        for path in paths:
            self.stdout.write(path)

    def _handle_print(self, options):
        if options["printer"] == "none":
            self.stdout.write("DRY_RUN=1")
            return

        spec = QRLabelSpec(
            width=options["width"],
            height=options["height"],
            qr_size=options["qr_size"],
            title=options["label_title"],
            subtitle=options["label_subtitle"],
            footer=options["footer"],
        )
        image = build_qr_label_image(options["text"], spec=spec)
        job = build_phomemo_m220_job(image, speed=options["speed"], density=options["density"])
        self.stdout.write("PRINTER=phomemo-m220")
        self.stdout.write(f"COMMAND_BYTES={len(job)}")

        if options.get("dry_run"):
            self.stdout.write("DRY_RUN=1")
            return

        usb_path = resolve_phomemo_m220_usb_path(options.get("usb_path"))
        if not usb_path:
            raise CommandError(
                f"No Phomemo M220 USB path configured. Pass --usb-path, set {PHOMEMO_M220_USB_PATH_ENV}, or run `python manage.py printers devices`."
            )
        written = write_windows_usb(
            usb_path,
            job,
            chunk_size=options["chunk_bytes"],
            delay_seconds=options["chunk_delay"],
        )
        self.stdout.write(self.style.SUCCESS(f"PHOMEMO_M220_WRITE_OK bytes={written}"))
