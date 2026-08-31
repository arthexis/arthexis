from __future__ import annotations

import ipaddress
import os
import re
import socket
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


HTTP01_WEBROOT_PATH = Path("/var/www/arthexis")
LETSENCRYPT_LIVE_PATH = Path("/etc/letsencrypt/live")
LETSENCRYPT_RENEWAL_PATH = Path("/etc/letsencrypt/renewal")


class CertbotError(RuntimeError):
    """Raised when certbot fails to request a certificate."""


class CertbotChallengeError(CertbotError):
    """Raised when ACME challenge validation fails during certbot issuance."""


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


def extract_live_certificate_paths_from_certbot_output(
    output: str,
) -> tuple[Path, Path] | None:
    """Return live certificate paths reported by certbot output when present."""

    cert_match = re.search(r"Certificate is saved at:\s*(\S+)", output)
    key_match = re.search(r"Key is saved at:\s*(\S+)", output)
    if not cert_match or not key_match:
        return None

    return Path(cert_match.group(1)), Path(key_match.group(1))




def ensure_certbot_available(*, sudo: str = "sudo") -> None:
    """Raise ``CertbotError`` with guidance when certbot is unavailable."""

    command = _with_sudo(["certbot", "--version"], sudo)
    try:
        _run_command(command)
    except FileNotFoundError as exc:  # pragma: no cover - thin wrapper
        missing_binary = Path(exc.filename or command[0]).name
        message = str(exc) or f"{missing_binary}: command not found"
        if missing_binary == "certbot":
            raise CertbotError(_build_missing_certbot_guidance(message)) from exc
        if missing_binary == "sudo":
            raise CertbotError(
                f"{message}\n"
                "The configured sudo executable is not available. "
                "Install sudo or rerun with sudo disabled if this process already has root access."
            ) from exc
        raise CertbotError(str(exc)) from exc
    except RuntimeError as exc:  # pragma: no cover - thin wrapper
        error_message = str(exc)
        if _is_missing_certbot_error(error_message):
            raise CertbotError(_build_missing_certbot_guidance(error_message)) from exc
        raise CertbotError(error_message) from exc

def request_certbot_certificate(
    *,
    domain: str,
    email: str | None = None,
    certificate_path: Path,
    certificate_key_path: Path,
    challenge_type: str = "nginx",
    dns_credential=None,
    dns_propagation_seconds: int = 900,
    dns_use_sandbox: bool | None = None,
    force_renewal: bool = False,
    sudo: str = "sudo",
) -> str:
    """Run certbot to request or renew certificates for *domain*.

    Args:
        force_renewal: Force certbot to re-issue an existing certificate.
    """

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
            force_renewal=force_renewal,
            sudo=sudo,
        )
    else:
        if sudo:
            _run_command(_with_sudo(["mkdir", "-p", str(HTTP01_WEBROOT_PATH)], sudo))
        else:
            HTTP01_WEBROOT_PATH.mkdir(parents=True, exist_ok=True)
        command = _build_http01_certbot_command(
            domain=domain,
            email=email,
            sudo=sudo,
            force_renewal=force_renewal,
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
        error_message = str(exc)
        cause: RuntimeError = exc
        if _is_live_directory_conflict_error(error_message, domain):
            try:
                if _repair_stale_live_directory(domain=domain, sudo=sudo):
                    return _run_command(command, env=env)
            except RuntimeError as repair_or_retry_exc:
                error_message = str(repair_or_retry_exc)
                cause = repair_or_retry_exc
        if _is_missing_certbot_error(error_message):
            raise CertbotError(_build_missing_certbot_guidance(error_message)) from cause
        if _is_challenge_failure_error(error_message):
            raise CertbotChallengeError(
                _build_challenge_failure_guidance(
                    base_message=error_message,
                    domain=domain,
                    challenge_type=challenge_type,
                )
            ) from cause
        raise CertbotError(error_message) from cause


def _is_live_directory_conflict_error(message: str, domain: str) -> bool:
    """Return True when certbot reports an existing live-directory conflict."""

    return f"live directory exists for {domain}".lower() in message.lower()


def _repair_stale_live_directory(*, domain: str, sudo: str) -> bool:
    """Delete stale certbot live directories that have no renewal config."""

    if not domain or not re.fullmatch(r"[A-Za-z0-9.-]+", domain):
        return False

    base_live = LETSENCRYPT_LIVE_PATH.resolve(strict=False)
    base_renewal = LETSENCRYPT_RENEWAL_PATH.resolve(strict=False)
    live_directory = (LETSENCRYPT_LIVE_PATH / domain).resolve(strict=False)
    renewal_config = (LETSENCRYPT_RENEWAL_PATH / f"{domain}.conf").resolve(strict=False)

    if live_directory.parent != base_live or renewal_config.parent != base_renewal:
        return False

    if not _path_exists(live_directory, sudo=sudo):
        return False
    if _path_exists(renewal_config, sudo=sudo):
        return False

    _run_command(_with_sudo(["rm", "-rf", str(live_directory)], sudo))
    return True


def _path_exists(path: Path, *, sudo: str) -> bool:
    """Return True when *path* exists, using sudo when configured."""

    command = _with_sudo(["test", "-e", str(path)], sudo)
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False

    stderr = result.stderr.strip()
    raise RuntimeError(stderr or "Command failed: " + " ".join(command))


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
            "Install certbot, then rerun the HTTPS command."
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


def _is_challenge_failure_error(message: str) -> bool:
    """Return True when certbot output indicates an ACME challenge validation failure."""

    lowered = message.lower()
    return "some challenges have failed" in lowered


def _build_challenge_failure_guidance(
    *,
    base_message: str,
    domain: str,
    challenge_type: str,
) -> str:
    """Build actionable guidance when certbot challenge validation fails."""

    hints = [
        base_message,
        (
            f"Challenge validation failed for {domain}. Confirm the hostname resolves to this "
            "server and that Let's Encrypt can reach it from the public internet."
        ),
    ]
    if challenge_type == "godaddy":
        hints.append(
            "Using DNS-01: verify GoDaddy API credentials, propagation delay, and matching authoritative DNS zone."
        )
    else:
        hints.extend(_build_http01_domain_resolution_hints(domain))
        hints.append(
            f"Using HTTP-01 webroot: ensure port 80 is open and serving /.well-known/acme-challenge/ from {HTTP01_WEBROOT_PATH}."
        )
    hints.append("Re-run with certbot -v and inspect /var/log/letsencrypt/letsencrypt.log for challenge-specific details.")
    return "\n".join(hints)


def _build_http01_domain_resolution_hints(domain: str) -> list[str]:
    """Return DNS-resolution hints that help triage HTTP-01 challenge failures."""

    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(
        socket.getaddrinfo,
        domain,
        80,
        0,
        0,
        socket.IPPROTO_TCP,
    )
    try:
        address_info = future.result(timeout=3)
    except TimeoutError:
        executor.shutdown(wait=False)
        return [
            (
                "DNS lookup timed out for the challenge domain. "
                f"{domain} did not return records before the resolver timeout."
            )
        ]
    except OSError as exc:
        executor.shutdown(wait=False)
        return [
            (
                "DNS lookup failed for the challenge domain. "
                f"{domain} could not be resolved ({exc})."
            )
        ]
    executor.shutdown(wait=True)

    addresses = sorted({item[4][0] for item in address_info if item[4]})
    if not addresses:
        return [
            (
                "DNS lookup returned no addresses for the challenge domain. "
                f"Verify A/AAAA records for {domain}."
            )
        ]

    hint = (
        f"DNS lookup for {domain} resolved to: {', '.join(addresses)}. "
        "Ensure these addresses route to this server."
    )

    if all(ipaddress.ip_address(address).is_private for address in addresses):
        hint += (
            " The resolved addresses are private; HTTP-01 validation requires "
            "publicly reachable A/AAAA records."
        )

    return [hint]


def _build_http01_certbot_command(
    *,
    domain: str,
    email: str | None,
    sudo: str,
    force_renewal: bool = False,
) -> list[str]:
    """Build a certbot command that uses webroot HTTP-01 validation."""

    command = _with_sudo(
        [
            "certbot",
            "certonly",
            "--webroot",
            "--webroot-path",
            str(HTTP01_WEBROOT_PATH),
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

    if force_renewal:
        command.append("--force-renewal")

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
    force_renewal: bool = False,
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
        "--non-interactive",
        "--agree-tos",
        "-d",
        domain,
    ])
    if email:
        command.extend(["--email", email])
    else:
        command.append("--register-unsafely-without-email")

    if force_renewal:
        command.append("--force-renewal")

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
    """Validate certificate files and cryptographic metadata for a domain.

    Filesystem permission issues are converted into verification messages so
    callers (for example status commands) do not crash when certificate paths
    are root-owned.
    """

    messages: list[str] = []
    ok = True

    def add_issue(message: str) -> None:
        nonlocal ok
        ok = False
        messages.append(message)

    def path_exists(path: Path, *, label: str) -> bool | None:
        """Return ``True`` when *path* is accessible and present.

        ``Path.exists`` can raise :class:`PermissionError` for restricted
        directories (such as ``/etc/letsencrypt/live``). In that case we record
        a concrete issue and continue with remaining checks.
        """

        try:
            return path.exists()
        except PermissionError as exc:
            add_issue(f"{label} path is not accessible at {path}: {exc}.")
            return None

    def check_path(path: Path | None, *, label: str) -> bool | None:
        """Check whether a path is configured, accessible, and present."""

        if not path:
            add_issue(f"{label} path is not set.")
            return None

        exists = path_exists(path, label=label)
        if exists is False:
            add_issue(f"{label} file not found at {path}.")
        return exists

    cert_exists: bool | None = check_path(certificate_path, label="Certificate")
    key_exists: bool | None = check_path(
        certificate_key_path,
        label="Certificate key",
    )

    if certificate_path and cert_exists is True:
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
        and cert_exists is True
        and key_exists is True
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
