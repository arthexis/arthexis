from __future__ import annotations

from io import StringIO

from django.core.management import call_command
import pytest

import apps.printers.management.commands.printers as printers_command


@pytest.mark.django_db
def test_printers_devices_lists_discovered_paths(monkeypatch) -> None:
    monkeypatch.setattr(printers_command, "resolve_phomemo_m220_usb_path", lambda path="": "USB-A")
    monkeypatch.setattr(printers_command, "iter_phomemo_m220_usb_paths", lambda: ["USB-A", "USB-B"])
    out = StringIO()

    call_command(printers_command.Command(), "devices", stdout=out)

    output = out.getvalue()
    assert "CONFIGURED_OR_DISCOVERED=USB-A" in output
    assert "USB-A" in output
    assert "USB-B" in output


def test_printers_print_label_dry_run() -> None:
    out = StringIO()

    call_command(printers_command.Command(), "print-label", "--text", "hello", "--printer", "none", stdout=out)

    output = out.getvalue()
    assert "PRINTER=phomemo-m220" not in output
    assert "COMMAND_BYTES=" not in output
    assert "DRY_RUN=1" in output


def test_printers_print_label_dry_run_with_device_builds_job() -> None:
    out = StringIO()

    call_command(
        printers_command.Command(),
        "print-label",
        "--text",
        "hello",
        "--printer",
        "phomemo-m220",
        "--dry-run",
        stdout=out,
    )

    output = out.getvalue()
    assert "PRINTER=phomemo-m220" in output
    assert "COMMAND_BYTES=" in output
    assert "DRY_RUN=1" in output
