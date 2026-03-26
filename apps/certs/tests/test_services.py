from __future__ import annotations

from concurrent.futures import TimeoutError
from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.certs import services

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

def test_request_certbot_certificate_without_sudo_omits_empty_prefix(
    monkeypatch, tmp_path
):
    captured: dict[str, list[str]] = {}

    def fake_run(command: list[str], *, env=None):  # noqa: ARG001
        captured["command"] = command
        return "ok"

    monkeypatch.setattr(services, "_run_command", fake_run)
    monkeypatch.setattr(services, "HTTP01_WEBROOT_PATH", tmp_path / "acme-webroot")

    services.request_certbot_certificate(
        domain="example.com",
        email=None,
        certificate_path=tmp_path / "fullchain.pem",
        certificate_key_path=tmp_path / "privkey.pem",
        challenge_type="nginx",
        sudo="",
    )

    assert captured["command"][0] == "certbot"

def test_build_godaddy_certbot_command_honors_sandbox_override():
    """GoDaddy certbot env should honor explicit sandbox override values."""

    credential = SimpleNamespace(
        use_sandbox=True,
        default_domain="example.com",
        resolve_sigils=lambda name: {
            "api_key": "key",
            "api_secret": "secret",
            "customer_id": "",
        }.get(name),
    )

    _command, env = services._build_godaddy_certbot_command(
        domain="example.com",
        email=None,
        dns_credential=credential,
        dns_propagation_seconds=60,
        dns_use_sandbox=False,
        sudo="",
    )

    assert env["GODADDY_USE_SANDBOX"] == "0"

def test_request_certbot_certificate_missing_certbot_includes_supported_os_guidance(
    monkeypatch, tmp_path
):
    """Missing certbot errors should include actionable guidance for supported hosts."""

    def fake_run(command: list[str], *, env=None):  # noqa: ARG001
        raise RuntimeError("sudo: certbot: command not found")

    monkeypatch.setattr(services, "_run_command", fake_run)
    monkeypatch.setattr(services, "HTTP01_WEBROOT_PATH", tmp_path / "acme-webroot")
    monkeypatch.setattr(
        services,
        "_read_os_release_fields",
        lambda: {"ID": "ubuntu", "PRETTY_NAME": "Ubuntu 24.04 LTS"},
    )

    with pytest.raises(services.CertbotError) as exc_info:
        services.request_certbot_certificate(
            domain="example.com",
            email="ops@example.com",
            certificate_path=tmp_path / "fullchain.pem",
            certificate_key_path=tmp_path / "privkey.pem",
            sudo="",
        )

    message = str(exc_info.value)
    assert "sudo: certbot: command not found" in message
    assert "Ubuntu 22.04 / 24.04" in message
    assert "apt install -y certbot" in message

def test_request_certbot_certificate_missing_certbot_binary_without_sudo_uses_guidance(
    monkeypatch, tmp_path
):
    """Missing certbot binary should be mapped to CertbotError guidance without sudo."""

    def fake_run(command: list[str], *, env=None):  # noqa: ARG001
        raise FileNotFoundError(2, "No such file or directory", "certbot")

    monkeypatch.setattr(services, "_run_command", fake_run)
    monkeypatch.setattr(services, "HTTP01_WEBROOT_PATH", tmp_path / "acme-webroot")
    monkeypatch.setattr(
        services,
        "_read_os_release_fields",
        lambda: {"ID": "UBUNTU", "ID_LIKE": "Debian", "PRETTY_NAME": "Ubuntu 24.04 LTS"},
    )

    with pytest.raises(services.CertbotError) as exc_info:
        services.request_certbot_certificate(
            domain="example.com",
            email="ops@example.com",
            certificate_path=tmp_path / "fullchain.pem",
            certificate_key_path=tmp_path / "privkey.pem",
            sudo="",
        )

    message = str(exc_info.value)
    assert "No such file or directory" in message
    assert "Ubuntu 22.04 / 24.04 & Debian-based hosts" in message
    assert "Detected Debian-family OS" in message

def test_request_certbot_certificate_challenge_failure_raises_specific_exception(
    monkeypatch, tmp_path
):
    """ACME challenge failures should raise CertbotChallengeError with remediation hints."""

    def fake_run(command: list[str], *, env=None):  # noqa: ARG001
        raise RuntimeError(  # noqa: TRY003
            "Saving debug log to /var/log/letsencrypt/letsencrypt.log\n"
            "Some challenges have failed."
        )

    monkeypatch.setattr(services, "_run_command", fake_run)
    monkeypatch.setattr(services, "HTTP01_WEBROOT_PATH", tmp_path / "acme-webroot")
    monkeypatch.setattr(
        services.socket,
        "getaddrinfo",
        lambda _domain, port, *_args, **_kwargs: [
            (services.socket.AF_INET, services.socket.SOCK_STREAM, services.socket.IPPROTO_TCP, "", ("192.0.2.1", port)),
        ],
    )

    with pytest.raises(services.CertbotChallengeError) as exc_info:
        services.request_certbot_certificate(
            domain="example.com",
            email="ops@example.com",
            certificate_path=tmp_path / "fullchain.pem",
            certificate_key_path=tmp_path / "privkey.pem",
            challenge_type="nginx",
            sudo="",
        )

    message = str(exc_info.value)
    assert "Some challenges have failed" in message
    assert "Using HTTP-01 webroot" in message
    assert str(tmp_path / "acme-webroot") in message
    assert "/var/log/letsencrypt/letsencrypt.log" in message

def test_verify_certificate_handles_permission_error():
    """Permission errors while probing certificate paths should not crash verification."""

    class RestrictedPath:
        def __init__(self, value: str):
            self.value = value

        def __str__(self) -> str:
            return self.value

        def exists(self) -> bool:
            raise PermissionError("access denied")

    result = services.verify_certificate(
        domain="example.com",
        certificate_path=RestrictedPath("/etc/letsencrypt/live/example/fullchain.pem"),
        certificate_key_path=RestrictedPath("/etc/letsencrypt/live/example/privkey.pem"),
        sudo="",
    )

    assert result.ok is False
    assert any(
        "Certificate path is not accessible" in message for message in result.messages
    )
    assert any(
        "Certificate key path is not accessible" in message
        for message in result.messages
    )

def test_ensure_certbot_available_missing_sudo_reports_sudo_guidance(monkeypatch):
    """Missing sudo binary should return sudo-specific guidance."""

    def fake_run(command: list[str], *, env=None):  # noqa: ARG001
        raise FileNotFoundError(2, "No such file or directory", "sudo")

    monkeypatch.setattr(services, "_run_command", fake_run)

    with pytest.raises(services.CertbotError) as exc_info:
        services.ensure_certbot_available()

    message = str(exc_info.value)
    assert "No such file or directory" in message
    assert "configured sudo executable is not available" in message
    assert "apt install -y certbot" not in message

def test_ensure_certbot_available_runtime_errors_are_wrapped(monkeypatch):
    """Non-missing-certbot runtime errors should still raise CertbotError."""

    def fake_run(command: list[str], *, env=None):  # noqa: ARG001
        raise RuntimeError("sudo: a password is required")

    monkeypatch.setattr(services, "_run_command", fake_run)

    with pytest.raises(services.CertbotError, match="sudo: a password is required"):
        services.ensure_certbot_available()

def test_ensure_certbot_available_missing_certbot_includes_supported_os_guidance(monkeypatch):
    """Missing certbot preflight checks should provide actionable install guidance."""

    def fake_run(command: list[str], *, env=None):  # noqa: ARG001
        raise RuntimeError("sudo: certbot: command not found")

    monkeypatch.setattr(services, "_run_command", fake_run)
    monkeypatch.setattr(
        services,
        "_read_os_release_fields",
        lambda: {"ID": "ubuntu", "PRETTY_NAME": "Ubuntu 24.04 LTS"},
    )

    with pytest.raises(services.CertbotError) as exc_info:
        services.ensure_certbot_available()

    message = str(exc_info.value)
    assert "sudo: certbot: command not found" in message
    assert "apt install -y certbot" in message
