from __future__ import annotations

import ipaddress
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


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


def _with_sudo(command: list[str], sudo: str) -> list[str]:
    """Prefix *command* with sudo when requested."""
    return [sudo, *command] if sudo else command


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
    """Generate a self-signed certificate using OpenSSL.

    Arthexis retains this capability for application-owned PKI such as local or
    OCPP deployments. Public-web ACME issuance belongs to gway-web.
    """
    cert_parent = certificate_path.parent
    key_parent = certificate_key_path.parent

    if sudo:
        _run_command(_with_sudo(["mkdir", "-p", str(cert_parent)], sudo))
        if key_parent != cert_parent:
            _run_command(_with_sudo(["mkdir", "-p", str(key_parent)], sudo))
    else:
        cert_parent.mkdir(parents=True, exist_ok=True)
        key_parent.mkdir(parents=True, exist_ok=True)

    config_path: Path | None = None
    config_contents = _build_self_signed_config(domain, subject_alt_names or [])
    if config_contents:
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as temp_file:
            temp_file.write(config_contents)
            config_path = Path(temp_file.name)

    command = [
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
        return _run_command(_with_sudo(command, sudo))
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


def _parse_cert_enddate(enddate_output: str) -> datetime:
    _, value = enddate_output.split("=", 1)
    parsed = datetime.strptime(value.strip(), "%b %d %H:%M:%S %Y %Z")
    return parsed.replace(tzinfo=UTC)


def get_certificate_expiration(
    *,
    certificate_path: Path,
    sudo: str = "sudo",
) -> datetime:
    enddate_output = _run_command(
        _with_sudo(
            ["openssl", "x509", "-noout", "-enddate", "-in", str(certificate_path)],
            sudo,
        )
    )
    return _parse_cert_enddate(enddate_output)


def verify_certificate(
    *,
    domain: str,
    certificate_path: Path | None,
    certificate_key_path: Path | None,
    sudo: str = "sudo",
) -> CertificateVerificationResult:
    """Validate certificate files and cryptographic metadata for a domain."""
    messages: list[str] = []
    ok = True

    def add_issue(message: str) -> None:
        nonlocal ok
        ok = False
        messages.append(message)

    def path_exists(path: Path, *, label: str) -> bool | None:
        try:
            return path.exists()
        except PermissionError as exc:
            add_issue(f"{label} path is not accessible at {path}: {exc}.")
            return None

    def check_path(path: Path | None, *, label: str) -> bool | None:
        if not path:
            add_issue(f"{label} path is not set.")
            return None
        exists = path_exists(path, label=label)
        if exists is False:
            add_issue(f"{label} file not found at {path}.")
        return exists

    cert_exists = check_path(certificate_path, label="Certificate")
    key_exists = check_path(certificate_key_path, label="Certificate key")

    if certificate_path and cert_exists is True:
        try:
            enddate = get_certificate_expiration(
                certificate_path=certificate_path,
                sudo=sudo,
            )
            if enddate < datetime.now(tz=UTC):
                add_issue(f"Certificate expired on {enddate.isoformat()}.")
            else:
                messages.append(f"Certificate valid until {enddate.isoformat()}.")
        except RuntimeError as exc:
            add_issue(f"Unable to read certificate expiry: {exc}.")

        try:
            subject_output = _run_command(
                _with_sudo(
                    [
                        "openssl",
                        "x509",
                        "-noout",
                        "-subject",
                        "-in",
                        str(certificate_path),
                    ],
                    sudo,
                )
            )
            san_output = _run_command(
                _with_sudo(
                    [
                        "openssl",
                        "x509",
                        "-noout",
                        "-ext",
                        "subjectAltName",
                        "-in",
                        str(certificate_path),
                    ],
                    sudo,
                )
            )
            if domain and domain not in subject_output and f"DNS:{domain}" not in san_output:
                add_issue(f"Certificate does not include domain {domain}.")
        except RuntimeError as exc:
            add_issue(f"Unable to read certificate subject information: {exc}.")

    if certificate_path and certificate_key_path and cert_exists is True and key_exists is True:
        try:
            cert_modulus = _run_command(
                _with_sudo(
                    [
                        "openssl",
                        "x509",
                        "-noout",
                        "-modulus",
                        "-in",
                        str(certificate_path),
                    ],
                    sudo,
                )
            )
            key_modulus = _run_command(
                _with_sudo(
                    [
                        "openssl",
                        "rsa",
                        "-noout",
                        "-modulus",
                        "-in",
                        str(certificate_key_path),
                    ],
                    sudo,
                )
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
