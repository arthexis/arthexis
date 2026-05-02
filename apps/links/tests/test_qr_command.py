"""Tests for the QR console command."""

from __future__ import annotations

from io import StringIO
from pathlib import Path
import stat

import pytest
from django.core.management import call_command

from apps.links.management.commands import qr as qr_command
from apps.links.models import QRRedirect, Reference
from apps.links.qr_printing import (
    QRLabelSpec,
    build_phomemo_m220_job,
    build_qr_label_image,
)

pytestmark = pytest.mark.django_db


def test_qr_print_text_writes_preview_png(tmp_path) -> None:
    output_path = tmp_path / "text-qr.png"
    stdout = StringIO()

    call_command(
        "qr",
        "print",
        "--text",
        "https://example.test/path",
        "--output",
        str(output_path),
        stdout=stdout,
    )

    assert output_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert "SOURCE=text" in stdout.getvalue()
    assert "PAYLOAD_BYTES=" in stdout.getvalue()


def test_qr_print_without_output_uses_secure_tempfile() -> None:
    stdout = StringIO()

    call_command(
        "qr",
        "print",
        "--wifi-ssid",
        "Office WiFi",
        "--wifi-password",
        "top-secret",
        stdout=stdout,
    )

    preview_line = next(line for line in stdout.getvalue().splitlines() if line.startswith("PREVIEW="))
    preview_path = preview_line.split("=", 1)[1]
    mode = stat.S_IMODE(Path(preview_path).stat().st_mode)

    assert Path(preview_path).exists()
    assert Path(preview_path).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert Path(preview_path).name.startswith("arthexis-qr-")
    assert mode == 0o600


def test_qr_print_wifi_does_not_echo_password(tmp_path) -> None:
    output_path = tmp_path / "wifi-qr.png"
    stdout = StringIO()

    call_command(
        "qr",
        "print",
        "--wifi-ssid",
        "Office WiFi",
        "--wifi-password",
        "do-not-print-this-secret",
        "--output",
        str(output_path),
        stdout=stdout,
    )

    output = stdout.getvalue()
    assert output_path.exists()
    assert "WIFI_SSID=Office WiFi" in output
    assert "do-not-print-this-secret" not in output


def test_qr_print_wifi_password_file_preserves_secret_spaces(tmp_path) -> None:
    password_file = tmp_path / "wifi-password.txt"
    password_file.write_text(" leading-and-trailing-secret \r\n", encoding="utf-8")

    password = qr_command.Command()._wifi_password_from_options(
        {"wifi_password_file": str(password_file)}
    )

    assert password == " leading-and-trailing-secret "


def test_qr_print_wifi_password_file_does_not_echo_secret(tmp_path) -> None:
    output_path = tmp_path / "wifi-file-qr.png"
    password_file = tmp_path / "wifi-password.txt"
    password_file.write_text("secret from file\n", encoding="utf-8")
    stdout = StringIO()

    call_command(
        "qr",
        "print",
        "--wifi-ssid",
        "Office WiFi",
        "--wifi-password-file",
        str(password_file),
        "--output",
        str(output_path),
        stdout=stdout,
    )

    output = stdout.getvalue()
    assert output_path.exists()
    assert "WIFI_SSID=Office WiFi" in output
    assert "secret from file" not in output


def test_qr_print_wifi_profile_uses_profile_lookup(monkeypatch, tmp_path) -> None:
    output_path = tmp_path / "wifi-profile-qr.png"
    stdout = StringIO()

    monkeypatch.setattr(
        qr_command.Command,
        "_read_windows_wifi_profile_password",
        lambda self, profile: "profile-secret",
    )

    call_command(
        "qr",
        "print",
        "--wifi-profile",
        "Office Profile",
        "--output",
        str(output_path),
        stdout=stdout,
    )

    output = stdout.getvalue()
    assert output_path.exists()
    assert "SOURCE=wifi-profile" in output
    assert "WIFI_PROFILE=Office Profile" in output
    assert "profile-secret" not in output


def test_qr_print_wifi_profile_nopass_skips_password_lookup(monkeypatch, tmp_path) -> None:
    output_path = tmp_path / "wifi-profile-open-qr.png"
    stdout = StringIO()

    def fail_password_lookup(self, profile):
        raise AssertionError("open profiles should not read saved keys")

    monkeypatch.setattr(
        qr_command.Command,
        "_read_windows_wifi_profile_password",
        fail_password_lookup,
    )

    call_command(
        "qr",
        "print",
        "--wifi-profile",
        "Guest Profile",
        "--wifi-auth",
        "nopass",
        "--output",
        str(output_path),
        stdout=stdout,
    )

    output = stdout.getvalue()
    assert output_path.exists()
    assert "SOURCE=wifi-profile" in output
    assert "WIFI_PROFILE=Guest Profile" in output


def test_qr_devices_lists_discovered_phomemo_paths(monkeypatch) -> None:
    stdout = StringIO()
    monkeypatch.setattr(qr_command, "resolve_phomemo_m220_usb_path", lambda path="": "USB-A")
    monkeypatch.setattr(qr_command, "iter_phomemo_m220_usb_paths", lambda: ["USB-A", "USB-B"])

    call_command("qr", "devices", stdout=stdout)

    output = stdout.getvalue()
    assert "CONFIGURED_OR_DISCOVERED=USB-A" in output
    assert "USB-A" in output
    assert "USB-B" in output


def test_windows_wifi_profile_password_parser_accepts_localized_label() -> None:
    output = "\n".join(
        [
            "Nombre de perfil     : Office WiFi",
            "Contenido de la clave     : localized-secret",
        ]
    )

    assert qr_command._extract_windows_wifi_profile_password(output) == "localized-secret"


def test_windows_wifi_profile_password_parser_preserves_secret_spaces() -> None:
    output = "Key Content     :  leading-and-trailing-secret "

    assert (
        qr_command._extract_windows_wifi_profile_password(output)
        == " leading-and-trailing-secret "
    )


def test_qr_print_reference_uses_database_value(settings, tmp_path) -> None:
    settings.MEDIA_ROOT = tmp_path / "media"
    reference = Reference.objects.create(
        alt_text="Support Portal",
        value="https://example.test/support",
    )
    output_path = tmp_path / "reference-qr.png"
    stdout = StringIO()

    call_command(
        "qr",
        "print",
        "--reference",
        str(reference.pk),
        "--output",
        str(output_path),
        stdout=stdout,
    )

    output = stdout.getvalue()
    assert output_path.exists()
    assert "SOURCE=reference" in output
    assert f"REFERENCE_ID={reference.pk}" in output


def test_qr_print_redirect_uses_base_url(tmp_path) -> None:
    redirect = QRRedirect.objects.create(
        slug="support",
        target_url="https://example.test/support-target",
        title="Support",
    )
    output_path = tmp_path / "redirect-qr.png"
    stdout = StringIO()

    call_command(
        "qr",
        "print",
        "--redirect",
        redirect.slug,
        "--base-url",
        "https://suite.example.test",
        "--output",
        str(output_path),
        stdout=stdout,
    )

    output = stdout.getvalue()
    assert output_path.exists()
    assert "SOURCE=redirect" in output
    assert "QR_REDIRECT_SLUG=support" in output
    assert "PAYLOAD_RELATIVE=1" not in output


def test_qr_print_phomemo_dry_run_builds_job_without_usb(tmp_path) -> None:
    output_path = tmp_path / "phomemo-qr.png"
    stdout = StringIO()

    call_command(
        "qr",
        "print",
        "--text",
        "https://example.test/phomemo",
        "--printer",
        "phomemo-m220",
        "--dry-run",
        "--output",
        str(output_path),
        stdout=stdout,
    )

    output = stdout.getvalue()
    assert output_path.exists()
    assert "PRINTER=phomemo-m220" in output
    assert "COMMAND_BYTES=" in output
    assert "DRY_RUN=1" in output


def test_phomemo_m220_job_contains_raster_payload() -> None:
    image = build_qr_label_image(
        "https://example.test",
        QRLabelSpec(title="QR CODE", subtitle="Example"),
    )

    job = build_phomemo_m220_job(image)

    assert job.startswith(b"\x1b\x4e\x0d\x02")
    assert b"\x1d\x76\x30\x00" in job
    assert job.endswith(b"\x1f\xf0\x05\x00\x1f\xf0\x03\x00")
