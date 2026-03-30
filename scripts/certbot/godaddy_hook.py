#!/usr/bin/env python3
"""Certbot manual DNS hook for creating/removing GoDaddy TXT records.

This script expects certbot manual mode environment variables and GoDaddy
credentials provided via environment variables.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

import dns.exception
import dns.resolver
import requests


DNS_POLL_INTERVAL_SECONDS = 5
HOOK_LOG_PATH = "/logs/certbot-godaddy-hook.log"


def _emit_log(message: str) -> None:
    """Emit hook diagnostics to stdout and /logs when writable."""

    print(message)
    try:
        with open(HOOK_LOG_PATH, "a", encoding="utf-8") as log_file:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            log_file.write(f"{timestamp} {message}\n")
    except OSError:
        # Best-effort file logging so hook behavior never depends on filesystem perms.
        pass


def _required_env(name: str) -> str:
    """Return required environment variable *name* or raise an error."""

    value = (os.environ.get(name) or "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _optional_env(name: str, default: str = "") -> str:
    """Return optional environment variable *name* with *default*."""

    return (os.environ.get(name) or default).strip()


def _zone_and_name(fqdn: str, zone_override: str = "") -> tuple[str, str]:
    """Split ACME record fqdn into GoDaddy zone domain and relative host name."""

    value = fqdn.rstrip(".")
    override = zone_override.strip().rstrip(".")
    if override:
        suffix = f".{override}"
        if value == override:
            return override, ""
        if not value.endswith(suffix):
            raise RuntimeError(
                f"Configured GODADDY_ZONE={override} does not match ACME challenge domain {value}."
            )
        return override, value[: -len(suffix)]

    labels = [part for part in value.split(".") if part]
    if len(labels) < 2:
        raise RuntimeError(f"Invalid DNS name for ACME challenge: {fqdn}")
    domain = ".".join(labels[-2:])
    host = ".".join(labels[:-2])
    _emit_log(f"GODADDY_ZONE not set; derived zone '{domain}' for challenge domain '{value}'.")
    return domain, host


def _godaddy_request(
    method: str, path: str, *, payload: Any | None = None
) -> requests.Response:
    """Execute a GoDaddy API request and return the raw response."""

    api_key = _required_env("GODADDY_API_KEY")
    api_secret = _required_env("GODADDY_API_SECRET")
    use_sandbox = _optional_env("GODADDY_USE_SANDBOX", "0") == "1"
    base_url = (
        "https://api.ote-godaddy.com" if use_sandbox else "https://api.godaddy.com"
    )

    headers = {
        "Authorization": f"sso-key {api_key}:{api_secret}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    customer_id = _optional_env("GODADDY_CUSTOMER_ID")
    if customer_id:
        headers["X-Shopper-Id"] = customer_id

    url = f"{base_url}{path}"
    return requests.request(method, url, json=payload, headers=headers, timeout=30)


def _fetch_existing_txt_values(zone: str, host: str) -> list[str]:
    """Return existing GoDaddy TXT values for the ACME host."""

    response = _godaddy_request("GET", f"/v1/domains/{zone}/records/TXT/{host or '@'}")
    if response.status_code == 404:
        return []
    if response.status_code >= 400:
        raise RuntimeError(
            f"GoDaddy TXT lookup failed: {response.status_code} {response.text}"
        )

    payload = response.json()
    if not isinstance(payload, list):
        return []
    return [str(item.get("data", "")).strip() for item in payload if isinstance(item, dict)]


def _query_authoritative_txt_values(zone: str, challenge_domain: str) -> set[str]:
    """Query authoritative DNS TXT values for *challenge_domain*."""

    resolver = dns.resolver.Resolver(configure=True)
    resolver.lifetime = 10
    nameserver_answers = resolver.resolve(zone, "NS")
    nameservers = [str(answer.target).rstrip(".") for answer in nameserver_answers]
    if not nameservers:
        raise RuntimeError(f"No authoritative nameservers found for zone {zone}.")

    observed_values: set[str] = set()
    errors: list[str] = []
    for nameserver in nameservers:
        try:
            ns_ips = [str(answer) for answer in resolver.resolve(nameserver, "A")]
        except dns.exception.DNSException as exc:
            errors.append(f"{nameserver}: {exc}")
            continue

        try:
            ns_ips.extend(str(answer) for answer in resolver.resolve(nameserver, "AAAA"))
        except dns.resolver.NoAnswer:
            pass
        except dns.exception.DNSException as exc:
            errors.append(f"{nameserver} (AAAA): {exc}")

        for ns_ip in ns_ips:
            ns_resolver = dns.resolver.Resolver(configure=False)
            ns_resolver.nameservers = [ns_ip]
            ns_resolver.lifetime = 5
            try:
                answers = ns_resolver.resolve(challenge_domain, "TXT")
            except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
                continue
            except dns.exception.DNSException as exc:
                errors.append(f"{nameserver} ({ns_ip}): {exc}")
                continue

            for answer in answers:
                text = b"".join(answer.strings).decode("utf-8").strip()
                if text:
                    observed_values.add(text)

    if not observed_values and errors:
        _emit_log("Authoritative DNS query errors: " + "; ".join(errors))

    return observed_values


def _wait_for_dns_txt_propagation(
    *,
    zone: str,
    challenge_domain: str,
    expected_value: str,
    timeout_seconds: int,
) -> None:
    """Wait until authoritative DNS serves the expected TXT value."""

    deadline = time.time() + max(0, timeout_seconds)
    while True:
        observed_values = _query_authoritative_txt_values(zone, challenge_domain)
        if expected_value in observed_values:
            _emit_log(
                f"Observed expected TXT for {challenge_domain} at authoritative DNS."
            )
            return

        if time.time() >= deadline:
            values = sorted(observed_values) if observed_values else ["<none>"]
            raise RuntimeError(
                "DNS propagation timeout waiting for TXT validation value. "
                f"Expected '{expected_value}' at {challenge_domain}; "
                f"observed {values}."
            )

        _emit_log(
            f"Waiting for TXT propagation at {challenge_domain}; "
            f"observed {sorted(observed_values) if observed_values else ['<none>']}"
        )
        time.sleep(DNS_POLL_INTERVAL_SECONDS)


def _upsert_txt_record() -> None:
    """Create or overwrite the ACME TXT record required for DNS-01 validation."""

    fqdn = _required_env("CERTBOT_DOMAIN")
    validation = _required_env("CERTBOT_VALIDATION")
    challenge_domain = (
        f"_acme-challenge.{fqdn}"
        if not fqdn.startswith("*.")
        else f"_acme-challenge.{fqdn[2:]}"
    )
    zone, host = _zone_and_name(challenge_domain, _optional_env("GODADDY_ZONE"))

    existing_values = _fetch_existing_txt_values(zone, host)
    if existing_values:
        _emit_log(
            f"Replacing existing TXT records for {challenge_domain}: {existing_values}"
        )

    payload = [{"data": validation, "ttl": 600}]
    response = _godaddy_request(
        "PUT", f"/v1/domains/{zone}/records/TXT/{host or '@'}", payload=payload
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"GoDaddy auth hook failed: {response.status_code} {response.text}"
        )

    wait_seconds = int(_optional_env("GODADDY_DNS_WAIT_SECONDS", "300"))
    _wait_for_dns_txt_propagation(
        zone=zone,
        challenge_domain=challenge_domain,
        expected_value=validation,
        timeout_seconds=wait_seconds,
    )


def _cleanup_txt_record() -> None:
    """Remove ACME TXT record after validation completes."""

    fqdn = _required_env("CERTBOT_DOMAIN")
    challenge_domain = (
        f"_acme-challenge.{fqdn}"
        if not fqdn.startswith("*.")
        else f"_acme-challenge.{fqdn[2:]}"
    )
    zone, host = _zone_and_name(challenge_domain, _optional_env("GODADDY_ZONE"))
    response = _godaddy_request(
        "PUT", f"/v1/domains/{zone}/records/TXT/{host or '@'}", payload=[]
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"GoDaddy cleanup hook failed: {response.status_code} {response.text}"
        )


def main(argv: list[str]) -> int:
    """Dispatch certbot hook action from CLI args."""

    if len(argv) < 2:
        raise RuntimeError("Usage: godaddy_hook.py <auth|cleanup>")
    action = argv[1].strip().lower()
    if action == "auth":
        _upsert_txt_record()
        return 0
    if action == "cleanup":
        _cleanup_txt_record()
        return 0
    raise RuntimeError(f"Unknown action: {action}")


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except Exception as exc:  # pragma: no cover - CLI error path
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
