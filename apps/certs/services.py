from __future__ import annotations

import ipaddress
import subprocess
import tempfile
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
    validation_provider: str | None = None,
    dns_api_key: str | None = None,
    dns_api_secret: str | None = None,
    dns_propagation_seconds: int = 60,
) -> str:
    """Run certbot to request or renew certificates for *domain*."""

    base_dir = certificate_path.parent
    base_dir_key = certificate_key_path.parent
    if sudo:
        subprocess.run([sudo, "mkdir", "-p", str(base_dir)], check=True)
        if base_dir_key != base_dir:
            subprocess.run([sudo, "mkdir", "-p", str(base_dir_key)], check=True)

    command = [
        "certbot",
        "certonly",
        "--agree-tos",
        "--non-interactive",
        "-d",
        domain,
    ]

    provider = (validation_provider or "").strip().lower()
    temp_credentials: Path | None = None
    if provider == "godaddy":
        if not dns_api_key or not dns_api_secret:
            raise CertbotError(
                "GoDaddy DNS validation requires DNS API key and secret."
            )
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as handle:
            handle.write(f"dns_godaddy_key = {dns_api_key}\n")
            handle.write(f"dns_godaddy_secret = {dns_api_secret}\n")
            temp_credentials = Path(handle.name)
        temp_credentials.chmod(0o600)
        command.extend(
            [
                "--authenticator",
                "dns-godaddy",
                "--dns-godaddy-credentials",
                str(temp_credentials),
                "--dns-godaddy-propagation-seconds",
                str(max(0, int(dns_propagation_seconds))),
            ]
        )
    else:
        command.append("--nginx")

    if email:
        command.extend(["--email", email])
    else:
        command.append("--register-unsafely-without-email")

    if sudo:
        command = [sudo, *command]

    try:
        return _run_command(command)
    except RuntimeError as exc:  # pragma: no cover - thin wrapper
        raise CertbotError(str(exc)) from exc
    finally:
        if temp_credentials:
            temp_credentials.unlink(missing_ok=True)


def generate_self_signed_certificate(
    *,
    domain: str,
    certificate_path: Path,
    certificate_key_path: Path,
    days_valid: int,
    key_length: int,
    subject_alt_names: list[str] | None = None,
    sudo: str = "sudo",
) -> str:
    """Generate a self-signed certificate using openssl."""

    cert_parent = certificate_path.parent
    key_parent = certificate_key_path.parent

    if sudo:
        subprocess.run([sudo, "mkdir", "-p", str(cert_parent)], check=True)
        subprocess.run([sudo, "mkdir", "-p", str(key_parent)], check=True)

    config_path: Path | None = None
    config_contents = _build_self_signed_config(domain, subject_alt_names or [])
    if config_contents:
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as temp_file:
            temp_file.write(config_contents)
            config_path = Path(temp_file.name)

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
    if config_path:
        command.extend(["-config", str(config_path), "-extensions", "v3_req"])

    try:
        return _run_command(command)
    except RuntimeError as exc:  # pragma: no cover - thin wrapper
        raise SelfSignedError(str(exc)) from exc
    finally:
        if config_path:
            config_path.unlink(missing_ok=True)


def _build_self_signed_config(domain: str, subject_alt_names: list[str]) -> str:
    entries = _format_subject_alt_name_entries(subject_alt_names)
    if not entries:
        return ""
    entries_block = "\n".join(entries)
    return "\n".join(
        [
            "[req]",
            "distinguished_name = req_distinguished_name",
            "x509_extensions = v3_req",
            "prompt = no",
            "",
            "[req_distinguished_name]",
            f"CN = {domain}",
            "",
            "[v3_req]",
            "subjectAltName = @alt_names",
            "",
            "[alt_names]",
            entries_block,
            "",
        ]
    )


def _format_subject_alt_name_entries(subject_alt_names: list[str]) -> list[str]:
    entries: list[str] = []
    seen: set[tuple[str, str]] = set()
    dns_index = 1
    ip_index = 1

    for raw in subject_alt_names:
        value = str(raw or "").strip()
        if not value:
            continue

        prefix = ""
        candidate = value
        if ":" in value:
            prefix, candidate = value.split(":", 1)
            if prefix.lower() not in {"dns", "ip"}:
                prefix = ""
                candidate = value
            else:
                prefix = prefix.lower()
                candidate = candidate.strip()

        if not prefix:
            try:
                ipaddress.ip_address(candidate)
            except ValueError:
                prefix = "dns"
            else:
                prefix = "ip"

        key = (prefix, candidate)
        if key in seen:
            continue
        seen.add(key)

        if prefix == "ip":
            entries.append(f"IP.{ip_index} = {candidate}")
            ip_index += 1
        else:
            entries.append(f"DNS.{dns_index} = {candidate}")
            dns_index += 1

    return entries


def _with_sudo(command: list[str], sudo: str) -> list[str]:
    if sudo:
        return [sudo, *command]
    return command


def _parse_cert_enddate(enddate_output: str) -> datetime:
    _, value = enddate_output.split("=", 1)
    parsed = datetime.strptime(value.strip(), "%b %d %H:%M:%S %Y %Z")
    return parsed.replace(tzinfo=timezone.utc)


def get_certificate_expiration(
    *,
    certificate_path: Path,
    sudo: str = "sudo",
) -> datetime:
    enddate_output = _run_command(
        _with_sudo(["openssl", "x509", "-noout", "-enddate", "-in", str(certificate_path)], sudo)
    )
    return _parse_cert_enddate(enddate_output)


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
            enddate = get_certificate_expiration(certificate_path=certificate_path, sudo=sudo)
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
