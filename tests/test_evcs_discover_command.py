import json
from ipaddress import ip_address
from types import SimpleNamespace

import pytest
from django.core.management import call_command

from core.models import Reference
from ocpp.evcs_discovery import ConsoleEndpoint, build_console_url
from ocpp.management.commands.evcs_discover import Command


@pytest.mark.django_db
def test_scan_command_creates_references(monkeypatch):
    Reference.objects.all().delete()
    command = Command()

    def fake_discover(
        self,
        hosts,
        *,
        timeout,
        workers,
        nmap_path,
        full_scan,
        top_ports,
    ):
        assert hosts == [ip_address("192.0.2.1"), ip_address("192.0.2.2")]
        assert timeout == command.DEFAULT_TIMEOUT
        return {
            "CONREF": ConsoleEndpoint(host="192.0.2.1", port=9443, secure=True),
            "OTHER": ConsoleEndpoint(host="192.0.2.2", port=8080, secure=False),
        }

    monkeypatch.setattr(Command, "_discover", fake_discover)
    monkeypatch.setattr(
        Command,
        "_discover_hosts",
        lambda self, network, include_self, controller_ip, nmap_path: [
            ip_address("192.0.2.1"),
            ip_address("192.0.2.2"),
        ],
    )

    call_command("evcs_discover", "--network", "192.0.2.0/29", "--end", "192.0.2.2")

    ref1 = Reference.objects.get(alt_text="CONREF Console")
    assert ref1.value == build_console_url("192.0.2.1", 9443, True)
    assert ref1.method == "link"
    assert ref1.show_in_header is True

    ref2 = Reference.objects.get(alt_text="OTHER Console")
    assert ref2.value == build_console_url("192.0.2.2", 8080, False)


@pytest.mark.django_db
def test_scan_command_skips_loopback_hosts(monkeypatch):
    Reference.objects.all().delete()
    command = Command()

    def fake_discover(
        self,
        hosts,
        *,
        timeout,
        workers,
        nmap_path,
        full_scan,
        top_ports,
    ):
        return {"LOOP": ConsoleEndpoint(host="127.0.0.1", port=8900, secure=False)}

    monkeypatch.setattr(Command, "_discover", fake_discover)
    monkeypatch.setattr(
        Command,
        "_discover_hosts",
        lambda self, network, include_self, controller_ip, nmap_path: [
            ip_address("127.0.0.1"),
        ],
    )

    call_command("evcs_discover", "--network", "127.0.0.0/30")

    assert not Reference.objects.filter(alt_text="LOOP Console").exists()


@pytest.mark.django_db
def test_existing_reference_is_updated(monkeypatch):
    Reference.objects.all().delete()
    Reference.objects.create(
        alt_text="CONREF Console",
        value="http://old-host:8900",
        method="qr",
        show_in_header=False,
    )

    def fake_discover(
        self,
        hosts,
        *,
        timeout,
        workers,
        nmap_path,
        full_scan,
        top_ports,
    ):
        return {"CONREF": ConsoleEndpoint(host="192.0.2.10", port=8800, secure=False)}

    monkeypatch.setattr(Command, "_discover", fake_discover)
    monkeypatch.setattr(
        Command,
        "_discover_hosts",
        lambda self, network, include_self, controller_ip, nmap_path: [
            ip_address("192.0.2.10"),
        ],
    )

    call_command(
        "evcs_discover",
        "--network",
        "192.0.2.0/28",
        "--start",
        "192.0.2.10",
        "--end",
        "192.0.2.10",
    )

    ref = Reference.objects.get(alt_text="CONREF Console")
    assert ref.value == build_console_url("192.0.2.10", 8800, False)
    assert ref.method == "link"
    assert ref.show_in_header is True


def test_extract_serial_from_json():
    cmd = Command()
    response = SimpleNamespace(getheader=lambda name, default=None: "application/json")
    body = json.dumps({"chargePointSerialNumber": "CP-1234"}).encode()
    assert cmd._extract_serial(response, body) == "CP-1234"


def test_extract_serial_from_text_variants():
    cmd = Command()
    response = SimpleNamespace(getheader=lambda name, default=None: "text/html")
    body = b"<html><body>Serial Number: CP-5678</body></html>"
    assert cmd._extract_serial(response, body) == "CP-5678"

    body_json = json.dumps({"nested": {"serialNumber": "SER-99"}}).encode()
    response_json = SimpleNamespace(getheader=lambda name, default=None: "application/json")
    assert cmd._extract_serial(response_json, body_json) == "SER-99"


@pytest.mark.django_db
def test_discover_deduplicates_serials(monkeypatch):
    cmd = Command()
    hosts = [ip_address("192.0.2.1"), ip_address("192.0.2.2")]

    def fake_probe(
        self,
        host,
        *,
        timeout,
        nmap_path,
        full_scan,
        top_ports,
    ):
        if host == hosts[0]:
            return ("SERIAL", ConsoleEndpoint(host="192.0.2.1", port=8900, secure=False))
        return ("SERIAL", ConsoleEndpoint(host="192.0.2.2", port=8900, secure=False))

    monkeypatch.setattr(Command, "_probe_host", fake_probe)
    result = cmd._discover(
        hosts,
        timeout=0.1,
        workers=1,
        nmap_path="nmap",
        full_scan=False,
        top_ports=Command.DEFAULT_PORT,
    )
    assert result == {"SERIAL": ConsoleEndpoint(host="192.0.2.1", port=8900, secure=False)}
