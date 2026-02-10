from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.certs import services

pytestmark = pytest.mark.critical

def test_verify_certificate_success(tmp_path, monkeypatch):
    certificate_path = tmp_path / "fullchain.pem"
    certificate_key_path = tmp_path / "privkey.pem"
    certificate_path.write_text("cert")
    certificate_key_path.write_text("key")

    def fake_run_command(command: list[str]) -> str:
        joined = " ".join(command)
        if "-enddate" in joined:
            return "notAfter=Jun  1 12:00:00 2999 GMT"
        if "-subject" in joined:
            return "subject=CN=example.com"
        if "-ext subjectAltName" in joined:
            return "X509v3 Subject Alternative Name:\n    DNS:example.com"
        if "-modulus" in joined and "x509" in joined:
            return "Modulus=ABC"
        if "-modulus" in joined and "rsa" in joined:
            return "Modulus=ABC"
        raise AssertionError(f"Unexpected command: {command}")

    monkeypatch.setattr(services, "_run_command", fake_run_command)

    result = services.verify_certificate(
        domain="example.com",
        certificate_path=certificate_path,
        certificate_key_path=certificate_key_path,
        sudo="",
    )

    assert result.ok is True
    assert any("valid until" in message for message in result.messages)
    assert any("Certificate and key match" in message for message in result.messages)

def test_verify_certificate_detects_key_mismatch(tmp_path, monkeypatch):
    certificate_path = tmp_path / "fullchain.pem"
    certificate_key_path = tmp_path / "privkey.pem"
    certificate_path.write_text("cert")
    certificate_key_path.write_text("key")

    def fake_run_command(command: list[str]) -> str:
        joined = " ".join(command)
        if "-enddate" in joined:
            return "notAfter=Jun  1 12:00:00 2999 GMT"
        if "-subject" in joined:
            return "subject=CN=example.com"
        if "-ext subjectAltName" in joined:
            return "X509v3 Subject Alternative Name:\n    DNS:example.com"
        if "-modulus" in joined and "x509" in joined:
            return "Modulus=ABC"
        if "-modulus" in joined and "rsa" in joined:
            return "Modulus=DEF"
        raise AssertionError(f"Unexpected command: {command}")

    monkeypatch.setattr(services, "_run_command", fake_run_command)

    result = services.verify_certificate(
        domain="example.com",
        certificate_path=certificate_path,
        certificate_key_path=certificate_key_path,
        sudo="",
    )

    assert result.ok is False
    assert any("do not match" in message for message in result.messages)

def test_generate_self_signed_certificate_with_subject_alt_names(tmp_path, monkeypatch):
    certificate_path = tmp_path / "fullchain.pem"
    certificate_key_path = tmp_path / "privkey.pem"
    captured = {}

    def fake_run_command(command: list[str]) -> str:
        captured["command"] = command
        return "ok"

    monkeypatch.setattr(services, "_run_command", fake_run_command)

    services.generate_self_signed_certificate(
        domain="localhost",
        certificate_path=certificate_path,
        certificate_key_path=certificate_key_path,
        days_valid=30,
        key_length=2048,
        subject_alt_names=["localhost", "127.0.0.1"],
        sudo="",
    )

    command = captured["command"]
    assert "-config" in command
    assert "-extensions" in command


def test_build_godaddy_certbot_command_uses_preserve_env_and_absolute_hook_path():
    credential = SimpleNamespace(
        use_sandbox=True,
        default_domain="example.co.uk",
        resolve_sigils=lambda name: {
            "api_key": "key",
            "api_secret": "secret",
            "customer_id": "customer-1",
        }.get(name),
    )

    command, env = services._build_godaddy_certbot_command(
        domain="app.example.co.uk",
        email="ops@example.com",
        dns_credential=credential,
        dns_propagation_seconds=120,
        sudo="sudo",
    )

    assert command[:5] == [
        "sudo",
        "certbot",
        "--preserve-env",
        (
            "GODADDY_API_KEY,GODADDY_API_SECRET,GODADDY_USE_SANDBOX,"
            "GODADDY_DNS_WAIT_SECONDS,GODADDY_CUSTOMER_ID,GODADDY_ZONE"
        ),
        "certonly",
    ]
    hook_script = str(
        Path(__file__).resolve().parents[3]
        / "scripts"
        / "certbot"
        / "godaddy_hook.py"
    )
    assert f"{services.sys.executable} {hook_script} auth" in command
    assert f"{services.sys.executable} {hook_script} cleanup" in command
    assert env["GODADDY_ZONE"] == "example.co.uk"


def test_request_certbot_certificate_without_sudo_omits_empty_prefix(
    monkeypatch, tmp_path
):
    captured: dict[str, list[str]] = {}

    def fake_run(command: list[str], *, env=None):
        captured["command"] = command
        return "ok"

    monkeypatch.setattr(services, "_run_command", fake_run)

    services.request_certbot_certificate(
        domain="example.com",
        email=None,
        certificate_path=tmp_path / "fullchain.pem",
        certificate_key_path=tmp_path / "privkey.pem",
        challenge_type="nginx",
        sudo="",
    )

    assert captured["command"][0] == "certbot"
