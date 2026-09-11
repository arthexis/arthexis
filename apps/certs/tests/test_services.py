from datetime import UTC, datetime
from pathlib import Path

from apps.certs import services


def test_format_subject_alt_name_entries_deduplicates_dns_and_ip():
    entries = services._format_subject_alt_name_entries(
        ["example.com", "DNS:example.com", "127.0.0.1", "IP:127.0.0.1"]
    )

    assert entries == ["DNS.1 = example.com", "IP.1 = 127.0.0.1"]


def test_build_self_signed_config_contains_subject_alt_names():
    config = services._build_self_signed_config(
        "example.com",
        ["example.com", "10.0.0.1"],
    )

    assert "CN = example.com" in config
    assert "DNS.1 = example.com" in config
    assert "IP.1 = 10.0.0.1" in config


def test_parse_cert_enddate_returns_utc_datetime():
    parsed = services._parse_cert_enddate("notAfter=Sep 11 12:34:56 2027 GMT")

    assert parsed == datetime(2027, 9, 11, 12, 34, 56, tzinfo=UTC)


def test_generate_self_signed_certificate_without_sudo(monkeypatch, tmp_path):
    commands = []

    def fake_run_command(command):
        commands.append(command)
        return "generated"

    monkeypatch.setattr(services, "_run_command", fake_run_command)

    certificate_path = tmp_path / "certs" / "certificate.pem"
    key_path = tmp_path / "keys" / "private.pem"
    result = services.generate_self_signed_certificate(
        domain="charger.local",
        certificate_path=certificate_path,
        certificate_key_path=key_path,
        days_valid=30,
        key_length=2048,
        subject_alt_names=["charger.local", "192.168.1.2"],
        sudo="",
    )

    assert result == "generated"
    assert certificate_path.parent.is_dir()
    assert key_path.parent.is_dir()
    assert commands[0][0:3] == ["openssl", "req", "-x509"]
    subject_index = commands[0].index("-subj")
    assert commands[0][subject_index + 1] == "/CN=charger.local"


def test_verify_certificate_reports_missing_paths(tmp_path):
    result = services.verify_certificate(
        domain="charger.local",
        certificate_path=Path(tmp_path / "missing-cert.pem"),
        certificate_key_path=Path(tmp_path / "missing-key.pem"),
        sudo="",
    )

    assert not result.ok
    assert "Certificate file not found" in result.summary
    assert "Certificate key file not found" in result.summary
