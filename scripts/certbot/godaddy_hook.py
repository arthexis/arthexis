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

import requests


def _required_env(name: str) -> str:
    """Return required environment variable *name* or raise an error."""

    value = (os.environ.get(name) or "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _optional_env(name: str, default: str = "") -> str:
    """Return optional environment variable *name* with *default*."""

    return (os.environ.get(name) or default).strip()


def _zone_and_name(fqdn: str) -> tuple[str, str]:
    """Split ACME record fqdn into GoDaddy zone domain and relative host name."""

    value = fqdn.rstrip(".")
    labels = [part for part in value.split(".") if part]
    if len(labels) < 2:
        raise RuntimeError(f"Invalid DNS name for ACME challenge: {fqdn}")
    domain = ".".join(labels[-2:])
    host = ".".join(labels[:-2])
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


def _upsert_txt_record() -> None:
    """Create or overwrite the ACME TXT record required for DNS-01 validation."""

    fqdn = _required_env("CERTBOT_DOMAIN")
    validation = _required_env("CERTBOT_VALIDATION")
    challenge_domain = (
        f"_acme-challenge.{fqdn}"
        if not fqdn.startswith("*.")
        else f"_acme-challenge.{fqdn[2:]}"
    )
    zone, host = _zone_and_name(challenge_domain)
    payload = [{"data": validation, "ttl": 600}]
    response = _godaddy_request(
        "PUT", f"/v1/domains/{zone}/records/TXT/{host or '@'}", payload=payload
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"GoDaddy auth hook failed: {response.status_code} {response.text}"
        )

    wait_seconds = int(_optional_env("GODADDY_DNS_WAIT_SECONDS", "120") or "120")
    if wait_seconds > 0:
        time.sleep(wait_seconds)


def _cleanup_txt_record() -> None:
    """Remove ACME TXT record after validation completes."""

    fqdn = _required_env("CERTBOT_DOMAIN")
    challenge_domain = (
        f"_acme-challenge.{fqdn}"
        if not fqdn.startswith("*.")
        else f"_acme-challenge.{fqdn[2:]}"
    )
    zone, host = _zone_and_name(challenge_domain)
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
