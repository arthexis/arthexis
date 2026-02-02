from __future__ import annotations

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
