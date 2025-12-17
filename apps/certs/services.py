from __future__ import annotations

import subprocess
from pathlib import Path


class CertbotError(RuntimeError):
    """Raised when certbot fails to request a certificate."""


class SelfSignedError(RuntimeError):
    """Raised when self-signed certificate generation fails."""


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
