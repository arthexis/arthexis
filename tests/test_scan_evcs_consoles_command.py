import json
from ipaddress import ip_address
from types import SimpleNamespace

import pytest
from django.core.management import call_command

from core.models import Reference
from ocpp.management.commands.scan_evcs_consoles import Command


@pytest.mark.django_db
def test_scan_command_creates_references(monkeypatch):
    command = Command()

    def fake_discover(self, hosts, timeout, workers):
        assert hosts == [ip_address("192.0.2.1"), ip_address("192.0.2.2")]
        assert timeout == command.DEFAULT_TIMEOUT
        return {"CONREF": "192.0.2.1", "OTHER": "192.0.2.2"}

    monkeypatch.setattr(Command, "_discover", fake_discover)

    call_command("scan_evcs_consoles", "--network", "192.0.2.0/29", "--end", "192.0.2.2")

    ref1 = Reference.objects.get(alt_text="CONREF Console")
    assert ref1.value == "http://192.0.2.1:8900"
    assert ref1.method == "link"
    assert ref1.show_in_header is True

    ref2 = Reference.objects.get(alt_text="OTHER Console")
    assert ref2.value == "http://192.0.2.2:8900"


@pytest.mark.django_db
def test_existing_reference_is_updated(monkeypatch):
    Reference.objects.create(
        alt_text="CONREF Console",
        value="http://old-host:8900",
        method="qr",
        show_in_header=False,
    )

    def fake_discover(self, hosts, timeout, workers):
        return {"CONREF": "192.0.2.10"}

    monkeypatch.setattr(Command, "_discover", fake_discover)

    call_command("scan_evcs_consoles", "--network", "192.0.2.0/28", "--start", "192.0.2.10", "--end", "192.0.2.10")

    ref = Reference.objects.get(alt_text="CONREF Console")
    assert ref.value == "http://192.0.2.10:8900"
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

    def fake_probe(self, host, *, timeout):
        if host == hosts[0]:
            return ("SERIAL", "192.0.2.1")
        return ("SERIAL", "192.0.2.2")

    monkeypatch.setattr(Command, "_probe_host", fake_probe)
    result = cmd._discover(hosts, timeout=0.1, workers=1)
    assert result == {"SERIAL": "192.0.2.1"}
