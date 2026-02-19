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
        dns_use_sandbox=None,
        sudo="sudo",
    )

    assert command[:2] == [
        "sudo",
        "--preserve-env=GODADDY_API_KEY,GODADDY_API_SECRET,GODADDY_USE_SANDBOX,"
        "GODADDY_DNS_WAIT_SECONDS,GODADDY_CUSTOMER_ID,GODADDY_ZONE",
    ]
    assert command[2] == "certbot"
    assert "certonly" in command
    hook_script = str(
        Path(__file__).resolve().parents[3]
        / "scripts"
        / "certbot"
        / "godaddy_hook.py"
    )
    assert f"{services.sys.executable} {hook_script} auth" in command
    assert f"{services.sys.executable} {hook_script} cleanup" in command
    assert env["GODADDY_ZONE"] == "example.co.uk"
    assert "--issuance-timeout" not in command


def test_extract_live_certificate_paths_from_certbot_output_returns_lineage_paths():
    output = "\n".join(
        [
            "Successfully received certificate.",
            "Certificate is saved at: /etc/letsencrypt/live/arthexis.com-0001/fullchain.pem",
            "Key is saved at: /etc/letsencrypt/live/arthexis.com-0001/privkey.pem",
        ]
    )

    extracted = services.extract_live_certificate_paths_from_certbot_output(output)

    assert extracted is not None
    cert_path, key_path = extracted
    assert cert_path == Path("/etc/letsencrypt/live/arthexis.com-0001/fullchain.pem")
    assert key_path == Path("/etc/letsencrypt/live/arthexis.com-0001/privkey.pem")


def test_extract_live_certificate_paths_from_certbot_output_returns_none_without_paths():
    output = "Certificate not due for renewal, no changes made."

    assert services.extract_live_certificate_paths_from_certbot_output(output) is None


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


def test_build_http01_certbot_command_uses_webroot_plugin():
    """HTTP-01 certbot command should use direct webroot validation."""

    command = services._build_http01_certbot_command(
        domain="example.com",
        email="ops@example.com",
        sudo="sudo",
    )

    assert command[:2] == ["sudo", "certbot"]
    assert "--webroot" in command
    assert "--webroot-path" in command
    assert str(services.HTTP01_WEBROOT_PATH) in command
    assert "--nginx" not in command
    assert "--standalone" not in command


def test_request_certbot_certificate_nginx_challenge_uses_webroot_validation(
    monkeypatch, tmp_path
):
    """Legacy nginx challenge type should run certbot with direct webroot validation."""

    captured: dict[str, list[str]] = {}

    def fake_run(command: list[str], *, env=None):  # noqa: ARG001
        captured["command"] = command
        return "ok"

    monkeypatch.setattr(services, "_run_command", fake_run)
    monkeypatch.setattr(services, "HTTP01_WEBROOT_PATH", tmp_path / "acme-webroot")

    services.request_certbot_certificate(
        domain="example.com",
        email="ops@example.com",
        certificate_path=tmp_path / "fullchain.pem",
        certificate_key_path=tmp_path / "privkey.pem",
        challenge_type="nginx",
        sudo="",
    )

    assert "--webroot" in captured["command"]
    assert "--webroot-path" in captured["command"]
    assert str(services.HTTP01_WEBROOT_PATH) in captured["command"]
    assert "--nginx" not in captured["command"]
    assert "--standalone" not in captured["command"]


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

    def fake_run(command: list[str], *, env=None):  # noqa: ARG001, TRY003
        raise RuntimeError(
            "Saving debug log to /var/log/letsencrypt/letsencrypt.log\n"
            "Some challenges have failed."
        )

    monkeypatch.setattr(services, "_run_command", fake_run)
    monkeypatch.setattr(services, "HTTP01_WEBROOT_PATH", tmp_path / "acme-webroot")

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


def test_request_certbot_certificate_godaddy_challenge_failure_includes_dns_hint(
    monkeypatch, tmp_path
):
    """DNS-01 challenge failures should include GoDaddy-specific troubleshooting guidance."""

    def fake_build(  # noqa: ARG001
        *,
        domain,
        email,
        dns_credential,
        dns_propagation_seconds,
        dns_use_sandbox,
        force_renewal,
        sudo,
    ):
        return ["certbot"], {}

    def fake_run(command: list[str], *, env=None):  # noqa: ARG001, TRY003
        raise RuntimeError("Some challenges have failed.")

    monkeypatch.setattr(services, "_build_godaddy_certbot_command", fake_build)
    monkeypatch.setattr(services, "_run_command", fake_run)

    with pytest.raises(services.CertbotChallengeError) as exc_info:
        services.request_certbot_certificate(
            domain="example.com",
            email="ops@example.com",
            certificate_path=tmp_path / "fullchain.pem",
            certificate_key_path=tmp_path / "privkey.pem",
            challenge_type="godaddy",
            dns_credential=SimpleNamespace(),
            sudo="",
        )

    assert "Using DNS-01" in str(exc_info.value)
