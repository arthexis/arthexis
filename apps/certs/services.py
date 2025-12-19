from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


class CertbotError(RuntimeError):
    """Raised when certbot fails to request a certificate."""


class SelfSignedError(RuntimeError):
    """Raised when self-signed certificate generation fails."""


@dataclass(frozen=True)
class CertificateVerificationResult:
    ok: bool
    messages: list[str]

    @property
    def summary(self) -> str:
        if not self.messages:
            return "Certificate verified."
        return "; ".join(self.messages)


def _run_command(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(stderr or "Command failed: " + " ".join(command))
    return result.stdout.strip()


def request_certbot_certificate(
    *,
    domain: str,
    email: str | None = None,
    certificate_path: Path,
    certificate_key_path: Path,
    sudo: str = "sudo",
) -> str:
    """Run certbot to request or renew certificates for *domain*."""

    base_dir = certificate_path.parent
    base_dir_key = certificate_key_path.parent
    if sudo:
        subprocess.run([sudo, "mkdir", "-p", str(base_dir)], check=True)
        if base_dir_key != base_dir:
            subprocess.run([sudo, "mkdir", "-p", str(base_dir_key)], check=True)

    command = [sudo, "certbot", "certonly", "--nginx", "-d", domain, "--agree-tos", "--non-interactive"]
    if email:
        command.extend(["--email", email])
    else:
        command.append("--register-unsafely-without-email")

    try:
        return _run_command(command)
    except RuntimeError as exc:  # pragma: no cover - thin wrapper
        raise CertbotError(str(exc)) from exc


def generate_self_signed_certificate(
    *,
    domain: str,
    certificate_path: Path,
    certificate_key_path: Path,
    days_valid: int,
    key_length: int,
    sudo: str = "sudo",
) -> str:
    """Generate a self-signed certificate using openssl."""

    cert_parent = certificate_path.parent
    key_parent = certificate_key_path.parent

    if sudo:
        subprocess.run([sudo, "mkdir", "-p", str(cert_parent)], check=True)
        subprocess.run([sudo, "mkdir", "-p", str(key_parent)], check=True)

    command = [
        sudo,
        "openssl",
        "req",
        "-x509",
        "-nodes",
        "-days",
        str(days_valid),
        "-newkey",
        f"rsa:{key_length}",
        "-subj",
        f"/CN={domain}",
        "-keyout",
        str(certificate_key_path),
        "-out",
        str(certificate_path),
    ]

    try:
        return _run_command(command)
    except RuntimeError as exc:  # pragma: no cover - thin wrapper
        raise SelfSignedError(str(exc)) from exc


def _with_sudo(command: list[str], sudo: str) -> list[str]:
    if sudo:
        return [sudo, *command]
    return command


def _parse_cert_enddate(enddate_output: str) -> datetime:
    _, value = enddate_output.split("=", 1)
    parsed = datetime.strptime(value.strip(), "%b %d %H:%M:%S %Y %Z")
    return parsed.replace(tzinfo=timezone.utc)


def verify_certificate(
    *,
    domain: str,
    certificate_path: Path | None,
    certificate_key_path: Path | None,
    sudo: str = "sudo",
) -> CertificateVerificationResult:
    messages: list[str] = []
    ok = True

    def add_issue(message: str) -> None:
        nonlocal ok
        ok = False
        messages.append(message)

    if not certificate_path:
        add_issue("Certificate path is not set.")
    elif not certificate_path.exists():
        add_issue(f"Certificate file not found at {certificate_path}.")

    if not certificate_key_path:
        add_issue("Certificate key path is not set.")
    elif not certificate_key_path.exists():
        add_issue(f"Certificate key file not found at {certificate_key_path}.")

    if certificate_path and certificate_path.exists():
        try:
            enddate_output = _run_command(
                _with_sudo(["openssl", "x509", "-noout", "-enddate", "-in", str(certificate_path)], sudo)
            )
            enddate = _parse_cert_enddate(enddate_output)
            if enddate < datetime.now(tz=timezone.utc):
                add_issue(f"Certificate expired on {enddate.isoformat()}.")
            else:
                messages.append(f"Certificate valid until {enddate.isoformat()}.")
        except RuntimeError as exc:
            add_issue(f"Unable to read certificate expiry: {exc}.")

        try:
            subject_output = _run_command(
                _with_sudo(["openssl", "x509", "-noout", "-subject", "-in", str(certificate_path)], sudo)
            )
            san_output = _run_command(
                _with_sudo(["openssl", "x509", "-noout", "-ext", "subjectAltName", "-in", str(certificate_path)], sudo)
            )
            domain_present = domain in subject_output or f"DNS:{domain}" in san_output
            if domain and not domain_present:
                add_issue(f"Certificate does not include domain {domain}.")
        except RuntimeError as exc:
            add_issue(f"Unable to read certificate subject information: {exc}.")

    if (
        certificate_path
        and certificate_key_path
        and certificate_path.exists()
        and certificate_key_path.exists()
    ):
        try:
            cert_modulus = _run_command(
                _with_sudo(["openssl", "x509", "-noout", "-modulus", "-in", str(certificate_path)], sudo)
            )
            key_modulus = _run_command(
                _with_sudo(["openssl", "rsa", "-noout", "-modulus", "-in", str(certificate_key_path)], sudo)
            )
            if cert_modulus != key_modulus:
                add_issue("Certificate and key do not match.")
            else:
                messages.append("Certificate and key match.")
        except RuntimeError as exc:
            add_issue(f"Unable to verify certificate key match: {exc}.")

    if ok and not messages:
        messages.append("Certificate verified.")

    return CertificateVerificationResult(ok=ok, messages=messages)
