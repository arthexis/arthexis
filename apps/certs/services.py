from __future__ import annotations

import ipaddress
import os
import subprocess
import sys
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


def _run_command(command: list[str], *, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        command, capture_output=True, text=True, check=False, env=env
    )
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
    challenge_type: str = "nginx",
    dns_credential=None,
    dns_propagation_seconds: int = 120,
    dns_use_sandbox: bool | None = None,
    sudo: str = "sudo",
) -> str:
    """Run certbot to request or renew certificates for *domain*."""

    # Certbot manages output locations under /etc/letsencrypt/live/<domain>/ by
    # default. We avoid pre-creating these directories so certbot can maintain
    # its expected symlink layout.
    _ = certificate_path, certificate_key_path

    if challenge_type == "godaddy":
        command, env = _build_godaddy_certbot_command(
            domain=domain,
            email=email,
            dns_credential=dns_credential,
            dns_propagation_seconds=dns_propagation_seconds,
            dns_use_sandbox=dns_use_sandbox,
            sudo=sudo,
        )
    else:
        command = _build_http01_certbot_command(
            domain=domain,
            email=email,
            sudo=sudo,
        )
        env = None

    try:
        return _run_command(command, env=env)
    except FileNotFoundError as exc:  # pragma: no cover - thin wrapper
        missing_binary = Path(exc.filename or command[0]).name
        if missing_binary == "certbot":
            message = str(exc) or "certbot: command not found"
            raise CertbotError(_build_missing_certbot_guidance(message)) from exc
        raise CertbotError(str(exc)) from exc
    except RuntimeError as exc:  # pragma: no cover - thin wrapper
        if _is_missing_certbot_error(str(exc)):
            raise CertbotError(_build_missing_certbot_guidance(str(exc))) from exc
        raise CertbotError(str(exc)) from exc


def _is_missing_certbot_error(message: str) -> bool:
    """Return True when stderr output indicates that certbot is not installed."""

    lowered = message.lower()
    return "certbot" in lowered and "command not found" in lowered


def _build_missing_certbot_guidance(base_message: str) -> str:
    """Build an actionable certbot installation message for supported Linux hosts."""

    distro = _read_os_release_fields()
    distro_id = distro.get("ID", "").strip().lower()
    distro_like = distro.get("ID_LIKE", "").strip().lower()
    distro_name = distro.get("PRETTY_NAME") or distro_id or "this host"

    guidance = [
        base_message,
        (
            f"certbot is required to provision Let's Encrypt certificates on {distro_name}. "
            "Install certbot, then rerun the https command."
        ),
        "Supported Arthexis hosts and recommended commands:",
        "- Ubuntu 22.04 / 24.04 & Debian-based hosts: sudo apt update && sudo apt install -y certbot",
    ]

    if distro_id in {"ubuntu", "debian"} or "debian" in distro_like:
        guidance.append(
            "Detected Debian-family OS, so the apt command above is the supported path."
        )
    else:
        guidance.append(
            "Detected a non-Debian OS; Arthexis install scripts target Ubuntu 22.04/24.04, "
            "so use one of those supported environments for managed HTTPS provisioning."
        )

    return "\n".join(guidance)


def _build_http01_certbot_command(*, domain: str, email: str | None, sudo: str) -> list[str]:
    """Build a certbot command that uses standalone HTTP-01 validation."""

    command = _with_sudo(
        [
            "certbot",
            "certonly",
            "--standalone",
            "--preferred-challenges",
            "http",
            "-d",
            domain,
            "--agree-tos",
            "--non-interactive",
        ],
        sudo,
    )

    if email:
        command.extend(["--email", email])
    else:
        command.append("--register-unsafely-without-email")

    return command


def _read_os_release_fields() -> dict[str, str]:
    """Return parsed key/value fields from /etc/os-release."""

    os_release = Path("/etc/os-release")
    if not os_release.exists():
        return {}

    fields: dict[str, str] = {}
    try:
        content = os_release.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return {}

    for line in content.splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        fields[key] = value.strip().strip('"')
    return fields


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
        with tempfile.NamedTemporaryFile(
            "w", delete=False, encoding="utf-8"
        ) as temp_file:
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


def _build_godaddy_certbot_command(
    *,
    domain: str,
    email: str | None,
    dns_credential,
    dns_propagation_seconds: int,
    dns_use_sandbox: bool | None,
    sudo: str,
) -> tuple[list[str], dict[str, str]]:
    """Build certbot command and environment for GoDaddy DNS-01 validation."""

    if dns_credential is None:
        raise CertbotError("GoDaddy DNS validation requires a DNS credential.")

    key = (dns_credential.resolve_sigils("api_key") or "").strip()
    secret = (dns_credential.resolve_sigils("api_secret") or "").strip()
    if not key or not secret:
        raise CertbotError("GoDaddy DNS validation requires API key and secret.")

    preserve_env_values = [
        "GODADDY_API_KEY",
        "GODADDY_API_SECRET",
        "GODADDY_USE_SANDBOX",
        "GODADDY_DNS_WAIT_SECONDS",
        "GODADDY_CUSTOMER_ID",
        "GODADDY_ZONE",
    ]
    command = _with_sudo(["certbot"], sudo, preserve_env=preserve_env_values)
    hook_script_path = Path(__file__).resolve().parents[2] / "scripts" / "certbot" / "godaddy_hook.py"
    hook_command = f"{sys.executable} {hook_script_path}"
    command.extend([
        "certonly",
        "--manual",
        "--preferred-challenges",
        "dns",
        "--manual-auth-hook",
        f"{hook_command} auth",
        "--manual-cleanup-hook",
        f"{hook_command} cleanup",
        "--manual-public-ip-logging-ok",
        "--non-interactive",
        "--agree-tos",
        "-d",
        domain,
    ])
    if email:
        command.extend(["--email", email])
    else:
        command.append("--register-unsafely-without-email")

    propagation_seconds = max(0, dns_propagation_seconds)

    env = os.environ.copy()
    env["GODADDY_API_KEY"] = key
    env["GODADDY_API_SECRET"] = secret
    use_sandbox = (
        dns_use_sandbox
        if dns_use_sandbox is not None
        else getattr(dns_credential, "use_sandbox", False)
    )
    env["GODADDY_USE_SANDBOX"] = "1" if use_sandbox else "0"
    env["GODADDY_DNS_WAIT_SECONDS"] = str(propagation_seconds)
    customer_id = (dns_credential.resolve_sigils("customer_id") or "").strip()
    if customer_id:
        env["GODADDY_CUSTOMER_ID"] = customer_id
    default_domain = (getattr(dns_credential, "default_domain", "") or "").strip()
    if default_domain:
        env["GODADDY_ZONE"] = default_domain

    return command, env


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


def _with_sudo(
    command: list[str],
    sudo: str,
    *,
    preserve_env: list[str] | None = None,
) -> list[str]:
    """Prefix *command* with sudo and optional environment preservation flags."""

    if sudo:
        sudo_command = [sudo]
        if preserve_env:
            sudo_command.append(f"--preserve-env={','.join(preserve_env)}")
        return [*sudo_command, *command]
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
            enddate = get_certificate_expiration(
                certificate_path=certificate_path, sudo=sudo
            )
            if enddate < datetime.now(tz=timezone.utc):
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
