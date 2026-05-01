"""Console QR generation and printing commands."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.links.models import QRRedirect, Reference
from apps.links.qr_printing import (
    DEFAULT_CHUNK_BYTES,
    DEFAULT_CHUNK_DELAY_SECONDS,
    DEFAULT_LABEL_HEIGHT,
    DEFAULT_LABEL_WIDTH,
    DEFAULT_QR_SIZE,
    PHOMEMO_M220_USB_PATH_ENV,
    QRLabelSpec,
    build_phomemo_m220_job,
    build_qr_label_image,
    build_wifi_payload,
    iter_phomemo_m220_usb_paths,
    resolve_phomemo_m220_usb_path,
    write_windows_usb,
)

WINDOWS_WIFI_PASSWORD_LABELS = frozenset(
    {
        "key content",
        "contenido de la clave",
        "contenido clave",
        "clave",
        "contenu de cle",
        "cle",
        "contenuto chiave",
        "chiave",
    }
)


@dataclass(frozen=True)
class QRSelection:
    """Resolved QR payload and safe display metadata."""

    source: str
    payload: str
    title: str = ""
    subtitle: str = ""
    footer: str = ""
    detail: str = ""


class Command(BaseCommand):
    """QR command group."""

    help = "QR command group. Use `qr print` to render or print labels."

    def add_arguments(self, parser):
        """Register QR subcommands."""

        subparsers = parser.add_subparsers(dest="action")
        subparsers.required = True

        print_parser = subparsers.add_parser(
            "print",
            help="Render a QR label preview and optionally print it.",
        )
        self._add_print_arguments(print_parser)

        devices_parser = subparsers.add_parser(
            "devices",
            help="List auto-discovered QR printer device paths.",
        )
        devices_parser.add_argument(
            "--printer",
            choices=["phomemo-m220"],
            default="phomemo-m220",
            help="Printer type to discover.",
        )

    def handle(self, *args, **options):
        """Dispatch QR actions."""

        action = options["action"]
        if action == "print":
            self._handle_print(options)
            return
        if action == "devices":
            self._handle_devices(options)
            return
        raise CommandError(f"Unsupported QR action: {action}")

    def _add_print_arguments(self, parser):
        source = parser.add_mutually_exclusive_group(required=True)
        source.add_argument("--text", help="Raw QR payload to encode.")
        source.add_argument(
            "--reference",
            help="Reference id, transaction UUID, or exact title to print.",
        )
        source.add_argument("--redirect", help="QRRedirect slug to print.")
        source.add_argument("--wifi-ssid", help="Build a Wi-Fi join QR for this SSID.")
        source.add_argument(
            "--wifi-profile",
            help="Build a Wi-Fi join QR from a saved Windows WLAN profile.",
        )

        parser.add_argument(
            "--wifi-password",
            default="",
            help="Wi-Fi password for --wifi-ssid. Prefer --wifi-password-file in scripts.",
        )
        parser.add_argument(
            "--wifi-password-file",
            help="Read the Wi-Fi password from a local file instead of argv.",
        )
        parser.add_argument(
            "--wifi-auth",
            default="WPA",
            choices=["WPA", "WEP", "nopass"],
            help="Wi-Fi authentication type.",
        )
        parser.add_argument(
            "--wifi-hidden",
            action="store_true",
            help="Mark the Wi-Fi network as hidden in the QR payload.",
        )
        parser.add_argument(
            "--base-url",
            help="Base URL for QRRedirect payloads. Defaults to PUBLIC_BASE_URL when configured.",
        )
        parser.add_argument(
            "--redirect-public-view",
            action="store_true",
            help="Encode the QRRedirect public view instead of the direct redirect path.",
        )
        parser.add_argument("--output", help="PNG preview path. Defaults to a temp file.")
        parser.add_argument("--label-title", help="Override the printed label title.")
        parser.add_argument("--label-subtitle", help="Override the printed label subtitle.")
        parser.add_argument("--footer", help="Override the printed label footer.")
        parser.add_argument("--width", type=int, default=DEFAULT_LABEL_WIDTH)
        parser.add_argument("--height", type=int, default=DEFAULT_LABEL_HEIGHT)
        parser.add_argument("--qr-size", type=int, default=DEFAULT_QR_SIZE)
        parser.add_argument(
            "--printer",
            choices=["none", "phomemo-m220"],
            default="none",
            help="Printer backend. Use phomemo-m220 to write a USB print job.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Build the preview and printer job without writing to USB.",
        )
        parser.add_argument(
            "--usb-path",
            help=(
                "Windows USB device path. Defaults to "
                f"{PHOMEMO_M220_USB_PATH_ENV} or auto-discovery."
            ),
        )
        parser.add_argument("--chunk-bytes", type=int, default=DEFAULT_CHUNK_BYTES)
        parser.add_argument("--chunk-delay", type=float, default=DEFAULT_CHUNK_DELAY_SECONDS)
        parser.add_argument("--speed", type=int, default=2)
        parser.add_argument("--density", type=int, default=15)

    def _handle_print(self, options):
        selection = self._resolve_selection(options)
        spec = QRLabelSpec(
            width=options["width"],
            height=options["height"],
            qr_size=options["qr_size"],
            title=options.get("label_title") or selection.title,
            subtitle=options.get("label_subtitle") or selection.subtitle,
            footer=options.get("footer") or selection.footer,
        )
        try:
            image = build_qr_label_image(selection.payload, spec=spec)
        except Exception as exc:
            raise CommandError(f"Failed to render QR label: {exc}") from exc

        output_path = self._resolve_output_path(options.get("output"))
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            image.convert("RGB").save(output_path, format="PNG")
        except OSError as exc:
            raise CommandError(f"Failed to write QR preview '{output_path}': {exc}") from exc

        self.stdout.write(f"SOURCE={selection.source}")
        if selection.detail:
            self.stdout.write(selection.detail)
        self.stdout.write(f"PREVIEW={output_path}")
        self.stdout.write(f"PAYLOAD_BYTES={len(selection.payload.encode('utf-8'))}")

        if options["printer"] == "none":
            if options.get("dry_run"):
                self.stdout.write("DRY_RUN=1")
            return

        if options["printer"] != "phomemo-m220":
            raise CommandError(f"Unsupported printer: {options['printer']}")

        try:
            job = build_phomemo_m220_job(
                image,
                speed=options["speed"],
                density=options["density"],
            )
        except Exception as exc:
            raise CommandError(f"Failed to build Phomemo M220 print job: {exc}") from exc

        self.stdout.write("PRINTER=phomemo-m220")
        self.stdout.write(f"COMMAND_BYTES={len(job)}")
        self.stdout.write(f"CHUNK_BYTES={options['chunk_bytes']}")
        self.stdout.write(f"CHUNK_DELAY_SECONDS={options['chunk_delay']}")
        if options.get("dry_run"):
            self.stdout.write("DRY_RUN=1")
            return

        try:
            usb_path = resolve_phomemo_m220_usb_path(options.get("usb_path"))
        except Exception as exc:
            raise CommandError(f"Failed to resolve Phomemo M220 USB path: {exc}") from exc
        if not usb_path:
            raise CommandError(
                "No Phomemo M220 USB path configured. Pass --usb-path, set "
                f"{PHOMEMO_M220_USB_PATH_ENV}, or run `python manage.py qr devices`."
            )
        try:
            written = write_windows_usb(
                usb_path,
                job,
                chunk_size=options["chunk_bytes"],
                delay_seconds=options["chunk_delay"],
            )
        except Exception as exc:
            raise CommandError(f"Failed to write Phomemo M220 USB job: {exc}") from exc
        self.stdout.write(self.style.SUCCESS(f"PHOMEMO_M220_WRITE_OK bytes={written}"))

    def _handle_devices(self, options):
        if options["printer"] != "phomemo-m220":
            raise CommandError(f"Unsupported printer: {options['printer']}")
        env_path = resolve_phomemo_m220_usb_path("")
        configured = bool(env_path)
        if configured:
            self.stdout.write(f"CONFIGURED_OR_DISCOVERED={env_path}")
        paths = list(iter_phomemo_m220_usb_paths())
        if not paths:
            self.stdout.write("No Phomemo M220 USBPRINT candidates found.")
            return
        for path in paths:
            self.stdout.write(path)

    def _resolve_selection(self, options) -> QRSelection:
        if options.get("text"):
            return QRSelection(
                source="text",
                payload=options["text"],
                title="QR CODE",
            )
        if options.get("reference"):
            return self._resolve_reference(options["reference"])
        if options.get("redirect"):
            return self._resolve_redirect(options["redirect"], options)
        if options.get("wifi_ssid"):
            return self._resolve_wifi_ssid(options)
        if options.get("wifi_profile"):
            return self._resolve_wifi_profile(options)
        raise CommandError("Choose one QR source.")

    def _resolve_reference(self, value: str) -> QRSelection:
        cleaned = value.strip()
        if not cleaned:
            raise CommandError("Reference lookup cannot be blank.")

        reference = None
        if cleaned.isdigit():
            reference = Reference.objects.filter(pk=int(cleaned)).first()
        if reference is None:
            try:
                reference_uuid = uuid.UUID(cleaned)
            except ValueError:
                reference_uuid = None
            if reference_uuid is not None:
                reference = Reference.objects.filter(transaction_uuid=reference_uuid).first()

        if reference is None:
            matches = list(Reference.objects.filter(alt_text__iexact=cleaned).order_by("pk")[:2])
            if len(matches) > 1:
                raise CommandError(
                    f"Multiple references match '{cleaned}'. Use the numeric id instead."
                )
            reference = matches[0] if matches else None

        if reference is None:
            raise CommandError(f"No reference found for '{cleaned}'.")
        if not reference.value:
            raise CommandError(f"Reference #{reference.pk} has no value to encode.")
        return QRSelection(
            source="reference",
            payload=reference.value,
            title=reference.alt_text,
            subtitle=f"Reference #{reference.pk}",
            detail=f"REFERENCE_ID={reference.pk}",
        )

    def _resolve_redirect(self, value: str, options) -> QRSelection:
        slug = value.strip()
        if not slug:
            raise CommandError("QRRedirect slug cannot be blank.")
        redirect = QRRedirect.objects.filter(slug__iexact=slug).first()
        if redirect is None:
            raise CommandError(f"No QR redirect found for slug '{slug}'.")
        path = redirect.public_path() if options.get("redirect_public_view") else redirect.redirect_path()
        payload = self._build_absolute_url(path, options.get("base_url"))
        detail = f"QR_REDIRECT_SLUG={redirect.slug}"
        if payload.startswith("/"):
            detail += "\nPAYLOAD_RELATIVE=1"
        return QRSelection(
            source="redirect",
            payload=payload,
            title=redirect.title or redirect.slug,
            subtitle=f"QR redirect {redirect.slug}",
            detail=detail,
        )

    def _resolve_wifi_ssid(self, options) -> QRSelection:
        ssid = options["wifi_ssid"].strip()
        password = self._wifi_password_from_options(options)
        auth_type = options["wifi_auth"]
        if auth_type != "nopass" and not password:
            raise CommandError("--wifi-password or --wifi-password-file is required unless --wifi-auth=nopass.")
        return QRSelection(
            source="wifi",
            payload=build_wifi_payload(
                ssid,
                password,
                auth_type=auth_type,
                hidden=options.get("wifi_hidden", False),
            ),
            title="WIFI",
            subtitle=ssid,
            footer="Scan to join",
            detail=f"WIFI_SSID={ssid}",
        )

    def _resolve_wifi_profile(self, options) -> QRSelection:
        profile = options["wifi_profile"].strip()
        if not profile:
            raise CommandError("Wi-Fi profile cannot be blank.")
        password = self._read_windows_wifi_profile_password(profile)
        return QRSelection(
            source="wifi-profile",
            payload=build_wifi_payload(
                profile,
                password,
                auth_type=options["wifi_auth"],
                hidden=options.get("wifi_hidden", False),
            ),
            title="WIFI",
            subtitle=profile,
            footer="Scan to join",
            detail=f"WIFI_PROFILE={profile}",
        )

    def _wifi_password_from_options(self, options) -> str:
        password_file = options.get("wifi_password_file")
        if password_file:
            path = Path(password_file).expanduser()
            try:
                return path.read_text(encoding="utf-8").rstrip("\r\n")
            except OSError as exc:
                raise CommandError(f"Unable to read --wifi-password-file '{path}': {exc}") from exc
        return options.get("wifi_password", "")

    def _read_windows_wifi_profile_password(self, profile: str) -> str:
        if sys.platform != "win32":
            raise CommandError("--wifi-profile can only read saved profiles on Windows.")
        try:
            output = subprocess.check_output(
                ["netsh", "wlan", "show", "profile", f"name={profile}", "key=clear"],
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise CommandError(f"Unable to read saved Wi-Fi profile '{profile}': {exc}") from exc
        password = _extract_windows_wifi_profile_password(output)
        if not password:
            raise CommandError(f"Saved Wi-Fi password was not available for profile '{profile}'.")
        return password

    def _build_absolute_url(self, path: str, base_url: str | None) -> str:
        base = (base_url or getattr(settings, "PUBLIC_BASE_URL", "") or "").strip()
        if not base:
            return path
        return urljoin(base.rstrip("/") + "/", path.lstrip("/"))

    def _resolve_output_path(self, output: str | None) -> Path:
        if output:
            return Path(output).expanduser().resolve()
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return Path(tempfile.gettempdir()) / f"arthexis-qr-{stamp}.png"


def _extract_windows_wifi_profile_password(output: str) -> str:
    for line in output.splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            continue
        if _normalize_windows_label(key) in WINDOWS_WIFI_PASSWORD_LABELS:
            return value.strip()
    return ""


def _normalize_windows_label(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    without_accents = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    return " ".join(without_accents.casefold().split())
