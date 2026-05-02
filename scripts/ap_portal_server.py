#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import mimetypes
import os
import re
import subprocess
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

BASE_DIR = Path(__file__).resolve().parents[1]
ASSETS_DIR = BASE_DIR / "config" / "data" / "ap_portal"
DEFAULT_STATE_DIR = BASE_DIR / ".state" / "ap_portal"
DEFAULT_SOURCE_URL = "https://github.com/arthexis/arthexis/blob/main/scripts/ap_portal_server.py"
DEFAULT_SUITE_LOGIN_HOST = "10.42.0.1"
DEFAULT_SUITE_LOGIN_PORT = 8888
DEFAULT_SUITE_LOGIN_PATH = "/login/"
DEFAULT_AUTHORIZED_REDIRECT_DELAY_MS = 3000
AUTHORIZED_MACS_PATH = DEFAULT_STATE_DIR / "authorized_macs.txt"
CONSENTS_PATH = DEFAULT_STATE_DIR / "consents.jsonl"
ACTIVITY_PATH = DEFAULT_STATE_DIR / "activity.jsonl"
NFT_TABLE_NAME = "arthexis_ap_portal"
AUTHORIZED_SET_NAME = "authorized_macs"
TERMS_VERSION = "qol-recording-v2"
MAX_PAYLOAD_BYTES = 1024 * 1024
ARP_TABLE_PATH = Path("/proc/net/arp")
NDISC_CACHE_PATH = Path("/proc/net/ndisc_cache")
LOCAL_DEVELOPMENT_MAC = "02:00:00:00:00:01"
TERMS_STATEMENT = (
    "I accept that my internet experience may be altered and recorded "
    "for quality of life purposes while using this access point."
)
MONITORING_NOTICE = (
    "Your activities on this AP ARE being monitored. Arthexis records gateway-visible "
    "connection metadata, portal requests, device identifiers, consent submissions, "
    "and authorization state for diagnostics, safety, and quality-of-life purposes."
)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAC_RE = re.compile(r"(?P<mac>([0-9a-f]{2}:){5}[0-9a-f]{2})", re.IGNORECASE)
LOGGER = logging.getLogger("arthexis.ap_portal")
JSONL_APPEND_LOCK = threading.Lock()


@dataclass(frozen=True)
class PortalConfig:
    bind: str
    port: int
    assets_dir: Path
    state_dir: Path
    authorized_macs_path: Path
    consents_path: Path
    activity_path: Path
    source_url: str
    suite_login_host: str = DEFAULT_SUITE_LOGIN_HOST
    suite_login_port: int = DEFAULT_SUITE_LOGIN_PORT
    suite_login_path: str = DEFAULT_SUITE_LOGIN_PATH
    authorized_redirect_delay_ms: int = DEFAULT_AUTHORIZED_REDIRECT_DELAY_MS
    sync_firewall: bool = True
    local_development_mac: str | None = None


class FirewallSyncError(RuntimeError):
    """Raised when the nftables ruleset cannot be updated."""


def _normalize_mac(value: str) -> str:
    return value.strip().lower()


def _validate_email(value: str) -> str:
    email = value.strip().lower()
    if not EMAIL_RE.match(email):
        raise ValueError("Enter a valid email address.")
    return email


def _read_text(path: Path) -> bytes:
    return path.read_bytes()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _client_ip_from_headers(headers: Any, fallback: str | None) -> str | None:
    real_ip = headers.get("X-Real-IP", "")
    if real_ip:
        return real_ip.strip()
    forwarded = headers.get("X-Forwarded-For", "")
    if forwarded:
        trusted_hops = [part.strip() for part in forwarded.split(",") if part.strip()]
        if trusted_hops:
            return trusted_hops[-1]
    return fallback


def _accept_terms_is_explicit(value: Any) -> bool:
    return value is True


def _form_accept_terms_is_explicit(value: str) -> bool:
    return value == "on"


def _ip_addresses_match(left: str, right: str) -> bool:
    try:
        return ipaddress.ip_address(left) == ipaddress.ip_address(right)
    except ValueError:
        return left == right


def _is_loopback_ip(value: str) -> bool:
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return value in {"localhost"}


def _normalize_url_path(value: str) -> str:
    path = str(value or DEFAULT_SUITE_LOGIN_PATH).strip() or DEFAULT_SUITE_LOGIN_PATH
    if not path.startswith("/"):
        path = f"/{path}"
    return path


def _host_for_suite_redirect(host: str, configured_host: str = "") -> str:
    raw_host = (configured_host or host or "").strip()
    if not raw_host:
        return "arthexis.net"
    if "://" in raw_host or raw_host.startswith("[") or raw_host.count(":") < 2:
        parsed = urlparse(raw_host if "://" in raw_host else f"//{raw_host}")
        hostname = parsed.hostname or "arthexis.net"
    else:
        hostname = raw_host
    try:
        parsed_ip = ipaddress.ip_address(hostname)
    except ValueError:
        return hostname
    if parsed_ip.version == 6:
        return f"[{hostname}]"
    return hostname


def _suite_login_url(host: str, *, configured_host: str = "", port: int, path: str) -> str:
    return f"http://{_host_for_suite_redirect(host, configured_host)}:{port}{_normalize_url_path(path)}"


class FirewallManager:
    def __init__(self, interface: str = "wlan0") -> None:
        self.interface = interface

    def sync(self, macs: set[str]) -> None:
        ruleset = self._render_ruleset(sorted(macs))
        if self._table_exists():
            ruleset = f"delete table inet {NFT_TABLE_NAME}\n{ruleset}"
        result = subprocess.run(
            ["nft", "-f", "-"],
            input=ruleset,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            details = (result.stderr or result.stdout or "nft apply failed").strip()
            raise FirewallSyncError(details)

    def _table_exists(self) -> bool:
        result = subprocess.run(
            ["nft", "list", "table", "inet", NFT_TABLE_NAME],
            check=False,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def _render_ruleset(self, macs: list[str]) -> str:
        set_block = [f"    set {AUTHORIZED_SET_NAME} {{", "        type ether_addr"]
        if macs:
            elements = ", ".join(macs)
            set_block.append(f"        elements = {{ {elements} }}")
        set_block.append("    }")

        return "\n".join(
            [
                f"table inet {NFT_TABLE_NAME} {{",
                *set_block,
                "",
                "    chain prerouting {",
                "        type nat hook prerouting priority dstnat; policy accept;",
                f'        iifname "{self.interface}" tcp dport 80 jump portal_redirect',
                "    }",
                "",
                "    chain portal_redirect {",
                f"        ether saddr @{AUTHORIZED_SET_NAME} return",
                "        meta l4proto tcp redirect to :80",
                "    }",
                "",
                "    chain forward {",
                "        type filter hook forward priority -5; policy accept;",
                f'        iifname "{self.interface}" ether saddr @{AUTHORIZED_SET_NAME} accept',
                f'        iifname "{self.interface}" drop',
                "    }",
                "}",
                "",
            ]
        )


class ActivityRecorder:
    def __init__(self, config: PortalConfig) -> None:
        self.config = config
        self.config.state_dir.mkdir(parents=True, exist_ok=True)

    def record(self, event_type: str, **fields: Any) -> dict[str, Any]:
        event = {
            "observed_at": _utc_now(),
            "event_type": event_type,
            "terms_version": TERMS_VERSION,
            "monitoring_notice": MONITORING_NOTICE,
        }
        event.update({key: value for key, value in fields.items() if value not in (None, "")})
        _append_jsonl(self.config.activity_path, event)
        return event

    def read_events(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.config.activity_path.exists():
            return []
        if limit <= 0:
            return []
        events: list[dict[str, Any]] = []
        with self.config.activity_path.open(encoding="utf-8") as handle:
            lines = deque(handle, maxlen=limit)
        for line in lines:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                events.append(payload)
        return events

    def read_consents(self) -> list[dict[str, Any]]:
        if not self.config.consents_path.exists():
            return []
        records: list[dict[str, Any]] = []
        for line in self.config.consents_path.read_text(encoding="utf-8").splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                records.append(payload)
        return records

    def client_summary(self, limit: int = 100) -> list[dict[str, Any]]:
        clients: dict[str, dict[str, Any]] = {}
        authorized = _read_authorized_macs(self.config.authorized_macs_path)

        for mac in authorized:
            clients.setdefault(
                mac,
                {
                    "mac_address": mac,
                    "authorized": True,
                    "event_count": 0,
                },
            )

        for consent in self.read_consents():
            mac = str(consent.get("mac_address") or "").lower()
            if not mac:
                continue
            entry = clients.setdefault(mac, {"mac_address": mac, "event_count": 0})
            entry["authorized"] = mac in authorized
            entry["email"] = consent.get("email")
            entry["accepted_at"] = consent.get("accepted_at")
            entry["last_ip_address"] = consent.get("ip_address")

        for event in self.read_events(limit=limit):
            mac = str(event.get("mac_address") or "").lower()
            key = mac or str(event.get("ip_address") or "unknown")
            entry = clients.setdefault(key, {"event_count": 0})
            if mac:
                entry["mac_address"] = mac
            if event.get("ip_address"):
                entry["last_ip_address"] = event.get("ip_address")
            entry["last_event_at"] = event.get("observed_at")
            entry["last_event_type"] = event.get("event_type")
            entry["event_count"] = int(entry.get("event_count") or 0) + 1
            entry.setdefault("authorized", key in authorized)

        return sorted(
            clients.values(),
            key=lambda item: str(item.get("last_event_at") or item.get("accepted_at") or ""),
            reverse=True,
        )


def _read_authorized_macs(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        _normalize_mac(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    line = json.dumps(payload, sort_keys=True) + "\n"
    with JSONL_APPEND_LOCK, path.open("a", encoding="utf-8") as handle:
        handle.write(line)


def _read_limited_request_body(headers: Any, rfile: Any) -> str:
    try:
        length = int(headers.get("Content-Length", "0") or "0")
    except ValueError as exc:
        raise ValueError("Invalid Content-Length.") from exc
    if length < 0:
        raise ValueError("Invalid Content-Length.")
    if length > MAX_PAYLOAD_BYTES:
        raise ValueError("Payload too large.")
    return rfile.read(length).decode("utf-8") if length else ""


def _parse_form_payload(raw: str) -> dict[str, Any]:
    form = parse_qs(raw)
    accept_terms = form.get("accept_terms", [""])[0]
    return {
        "email": form.get("email", [""])[0],
        "accept_terms": _form_accept_terms_is_explicit(accept_terms),
    }


class PortalState:
    def __init__(self, config: PortalConfig) -> None:
        self.config = config
        self._lock = threading.RLock()
        self._firewall = FirewallManager()
        self.activity = ActivityRecorder(config)
        self._authorized = _read_authorized_macs(self.config.authorized_macs_path)
        if self.config.sync_firewall:
            self._firewall.sync(self._authorized)

    def status_for_request(
        self,
        *,
        ip_address: str | None,
        user_agent: str,
        path: str,
        host: str,
    ) -> dict[str, Any]:
        mac_address = self.resolve_mac(ip_address)
        with self._lock:
            authorized = bool(mac_address and mac_address in self._authorized)
        authorized_redirect_url = (
            self.suite_login_url(host) if authorized else ""
        )
        self.activity.record(
            "status_check",
            ip_address=ip_address,
            mac_address=mac_address,
            authorized=authorized,
            user_agent=user_agent,
            path=path,
            host=host,
        )
        return {
            "authorized": authorized,
            "mac_address": mac_address,
            "authorized_redirect_url": authorized_redirect_url,
            "redirect_delay_ms": self.config.authorized_redirect_delay_ms,
            "terms_version": TERMS_VERSION,
            "terms_statement": TERMS_STATEMENT,
            "monitoring_notice": MONITORING_NOTICE,
            "source_code_url": self.config.source_url,
            "activity_recording": {
                "activity_log": str(self.config.activity_path),
                "consent_log": str(self.config.consents_path),
                "authorized_macs": str(self.config.authorized_macs_path),
            },
        }

    def record_request(
        self,
        *,
        ip_address: str | None,
        user_agent: str,
        method: str,
        path: str,
        host: str,
        referer: str,
    ) -> None:
        mac_address = self.resolve_mac(ip_address)
        authorized = bool(mac_address and mac_address in self._authorized)
        self.activity.record(
            "request",
            ip_address=ip_address,
            mac_address=mac_address,
            authorized=authorized,
            user_agent=user_agent,
            method=method,
            path=path,
            host=host,
            referer=referer,
        )

    def suite_login_url(self, host: str) -> str:
        return _suite_login_url(
            host,
            configured_host=self.config.suite_login_host,
            port=self.config.suite_login_port,
            path=self.config.suite_login_path,
        )

    def subscribe(
        self,
        *,
        email: str,
        accept_terms: bool,
        ip_address: str | None,
        user_agent: str,
        host: str,
    ) -> dict[str, Any]:
        if not accept_terms:
            raise ValueError("You must accept the access terms to continue.")

        normalized_email = _validate_email(email)
        mac_address = self.resolve_mac(ip_address)
        if not mac_address:
            self.activity.record(
                "consent_rejected",
                ip_address=ip_address,
                user_agent=user_agent,
                host=host,
                reason="missing_mac",
            )
            raise ValueError("Unable to identify this device on the access point yet.")

        record = {
            "accepted_at": _utc_now(),
            "email": normalized_email,
            "accept_terms": True,
            "terms_version": TERMS_VERSION,
            "terms_statement": TERMS_STATEMENT,
            "monitoring_notice": MONITORING_NOTICE,
            "source_code_url": self.config.source_url,
            "ip_address": ip_address or "",
            "mac_address": mac_address,
            "user_agent": user_agent,
        }

        with self._lock:
            already_authorized = mac_address in self._authorized
            if not already_authorized:
                previous_authorized = set(self._authorized)
                authorized_file_existed = self.config.authorized_macs_path.exists()
                next_authorized = set(self._authorized)
                next_authorized.add(mac_address)
                if self.config.sync_firewall:
                    self._firewall.sync(next_authorized)
                consent_rollback_position = self._consent_log_position()
                try:
                    self._write_authorized_macs(next_authorized)
                    self._append_consent(record)
                    self.activity.record(
                        "consent_accepted",
                        ip_address=ip_address,
                        mac_address=mac_address,
                        email=normalized_email,
                        already_authorized=already_authorized,
                        user_agent=user_agent,
                        host=host,
                    )
                except OSError as consent_error:
                    rollback_error = None
                    try:
                        self._restore_consent_log(consent_rollback_position)
                    except OSError as exc:
                        rollback_error = exc
                    try:
                        self._restore_authorized_macs(previous_authorized, existed=authorized_file_existed)
                    except OSError as exc:
                        if rollback_error is not None:
                            rollback_error.add_note(str(exc))
                        else:
                            rollback_error = exc
                    try:
                        if self.config.sync_firewall:
                            self._firewall.sync(previous_authorized)
                    except FirewallSyncError as exc:
                        if rollback_error is not None:
                            exc.add_note(f"file rollback failed: {rollback_error}")
                        raise
                    if rollback_error is not None:
                        raise rollback_error from consent_error
                    raise
                self._authorized = next_authorized
            else:
                consent_rollback_position = self._consent_log_position()
                try:
                    self._append_consent(record)
                    self.activity.record(
                        "consent_accepted",
                        ip_address=ip_address,
                        mac_address=mac_address,
                        email=normalized_email,
                        already_authorized=already_authorized,
                        user_agent=user_agent,
                        host=host,
                    )
                except OSError:
                    self._restore_consent_log(consent_rollback_position)
                    raise

        return {
            "authorized": True,
            "already_authorized": already_authorized,
            "mac_address": mac_address,
            "monitoring_notice": MONITORING_NOTICE,
            "source_code_url": self.config.source_url,
            "redirect_url": self.suite_login_url(host),
            "redirect_delay_ms": self.config.authorized_redirect_delay_ms,
        }

    def resolve_mac(self, ip_address: str | None) -> str | None:
        if not ip_address:
            return None
        if self.config.local_development_mac and _is_loopback_ip(ip_address):
            return self.config.local_development_mac

        mac_address = self._resolve_mac_from_arp(ip_address)
        if mac_address:
            return mac_address
        return self._resolve_mac_from_ndisc(ip_address)

    def _resolve_mac_from_arp(self, ip_address: str) -> str | None:
        try:
            arp_rows = ARP_TABLE_PATH.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None

        for row in arp_rows[1:]:
            fields = row.split()
            if len(fields) >= 4 and _ip_addresses_match(fields[0], ip_address):
                match = MAC_RE.fullmatch(fields[3])
                if match:
                    return _normalize_mac(match.group("mac"))
        return None

    def _resolve_mac_from_ndisc(self, ip_address: str) -> str | None:
        try:
            neighbor_rows = NDISC_CACHE_PATH.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None

        for row in neighbor_rows:
            fields = row.split()
            if fields and _ip_addresses_match(fields[0], ip_address):
                match = MAC_RE.search(row)
                if match:
                    return _normalize_mac(match.group("mac"))
        return None

    def _write_authorized_macs(self, macs: set[str]) -> None:
        lines = sorted(_normalize_mac(mac) for mac in macs if mac)
        payload = "\n".join(lines)
        if payload:
            payload += "\n"
        self.config.authorized_macs_path.write_text(payload, encoding="utf-8")

    def _restore_authorized_macs(self, macs: set[str], *, existed: bool) -> None:
        if existed:
            self._write_authorized_macs(macs)
        else:
            self.config.authorized_macs_path.unlink(missing_ok=True)

    def _append_consent(self, record: dict[str, Any]) -> None:
        _append_jsonl(self.config.consents_path, record)

    def _consent_log_position(self) -> int | None:
        if not self.config.consents_path.exists():
            return None
        return self.config.consents_path.stat().st_size

    def _restore_consent_log(self, position: int | None) -> None:
        if position is None:
            self.config.consents_path.unlink(missing_ok=True)
            return
        with self.config.consents_path.open("r+b") as handle:
            handle.truncate(position)


class PortalApplication:
    def __init__(self, config: PortalConfig) -> None:
        self.config = config
        self.state = PortalState(config)

    def handler_class(self) -> type[BaseHTTPRequestHandler]:
        app = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "ArthexisAPPortal/2.0"

            def log_message(self, format: str, *args: Any) -> None:
                LOGGER.info("%s - %s", self.address_string(), format % args)

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path == "/health":
                    self._json({"ok": True})
                    return
                if parsed.path == "/api/status":
                    self._json(
                        app.state.status_for_request(
                            ip_address=self._client_ip(),
                            user_agent=self.headers.get("User-Agent", ""),
                            path=parsed.path,
                            host=self.headers.get("Host", ""),
                        )
                    )
                    return
                if parsed.path == "/api/clients":
                    if not self._is_direct_local_request():
                        self._json({"error": "local_only"}, status=HTTPStatus.FORBIDDEN)
                        return
                    self._json({"clients": app.state.activity.client_summary()})
                    return

                if parsed.path in {"", "/"}:
                    self._record_request(parsed.path or "/")
                    self._serve_asset("index.html")
                    return
                asset_name = parsed.path.lstrip("/")
                if "/" in asset_name or asset_name.startswith("."):
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                self._record_request(parsed.path)
                self._serve_asset(asset_name)

            def do_POST(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path != "/api/subscribe":
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                try:
                    self._record_request(parsed.path)
                    data = self._read_payload()
                    result = app.state.subscribe(
                        email=str(data.get("email") or ""),
                        accept_terms=_accept_terms_is_explicit(data.get("accept_terms")),
                        ip_address=self._client_ip(),
                        user_agent=self.headers.get("User-Agent", ""),
                        host=self.headers.get("Host", ""),
                    )
                except ValueError as exc:
                    self._json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                    return
                except FirewallSyncError as exc:
                    LOGGER.exception("Firewall sync failed")
                    self._json(
                        {"error": "Unable to authorize this device right now.", "details": str(exc)},
                        status=HTTPStatus.INTERNAL_SERVER_ERROR,
                    )
                    return
                except OSError as exc:
                    LOGGER.exception("Consent audit logging failed")
                    self._json(
                        {"error": "Unable to record consent right now.", "details": str(exc)},
                        status=HTTPStatus.INTERNAL_SERVER_ERROR,
                    )
                    return
                self._json(result)

            def _client_ip(self) -> str | None:
                fallback = self.client_address[0] if self.client_address else None
                return _client_ip_from_headers(self.headers, fallback)

            def _is_direct_local_request(self) -> bool:
                fallback = self.client_address[0] if self.client_address else ""
                return fallback in {"127.0.0.1", "::1"} and not self.headers.get("X-Forwarded-For")

            def _record_request(self, path: str) -> None:
                app.state.record_request(
                    ip_address=self._client_ip(),
                    user_agent=self.headers.get("User-Agent", ""),
                    method=self.command,
                    path=path,
                    host=self.headers.get("Host", ""),
                    referer=self.headers.get("Referer", ""),
                )

            def _serve_asset(self, name: str) -> None:
                path = app.config.assets_dir / name
                if not path.exists() or not path.is_file():
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                body = _read_text(path)
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _read_payload(self) -> dict[str, Any]:
                raw = _read_limited_request_body(self.headers, self.rfile)
                content_type = self.headers.get("Content-Type", "")
                if "application/json" in content_type:
                    parsed = json.loads(raw or "{}")
                    if not isinstance(parsed, dict):
                        raise ValueError("Invalid request payload.")
                    return parsed
                return _parse_form_payload(raw)

            def _json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
                body = json.dumps(payload, sort_keys=True).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return Handler


def build_config(args: argparse.Namespace) -> PortalConfig:
    state_dir = Path(args.state_dir).expanduser().resolve()
    assets_dir = Path(args.assets_dir).expanduser().resolve()
    source_url = args.source_url or os.environ.get("ARTHEXIS_AP_SOURCE_URL") or DEFAULT_SOURCE_URL
    local_development_mac = (
        args.local_development_mac
        or os.environ.get("ARTHEXIS_AP_LOCAL_DEVELOPMENT_MAC")
        or (LOCAL_DEVELOPMENT_MAC if args.skip_firewall_sync else "")
    )
    if local_development_mac:
        local_development_mac = _normalize_mac(local_development_mac)
        if not MAC_RE.fullmatch(local_development_mac):
            raise ValueError(f"Invalid local development MAC: {local_development_mac}")
    return PortalConfig(
        bind=args.bind,
        port=args.port,
        assets_dir=assets_dir,
        state_dir=state_dir,
        authorized_macs_path=state_dir / "authorized_macs.txt",
        consents_path=state_dir / "consents.jsonl",
        activity_path=state_dir / "activity.jsonl",
        source_url=source_url,
        suite_login_host=args.suite_login_host,
        suite_login_port=args.suite_login_port,
        suite_login_path=_normalize_url_path(args.suite_login_path),
        authorized_redirect_delay_ms=args.authorized_redirect_delay_ms,
        sync_firewall=not args.skip_firewall_sync,
        local_development_mac=local_development_mac or None,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Arthexis AP consent and monitoring portal.")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9080)
    parser.add_argument("--assets-dir", default=str(ASSETS_DIR))
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    parser.add_argument("--source-url", default="")
    parser.add_argument("--suite-login-host", default=DEFAULT_SUITE_LOGIN_HOST)
    parser.add_argument("--suite-login-port", type=int, default=DEFAULT_SUITE_LOGIN_PORT)
    parser.add_argument("--suite-login-path", default=DEFAULT_SUITE_LOGIN_PATH)
    parser.add_argument(
        "--authorized-redirect-delay-ms",
        type=int,
        default=DEFAULT_AUTHORIZED_REDIRECT_DELAY_MS,
    )
    parser.add_argument(
        "--local-development-mac",
        default="",
        help=(
            "Loopback MAC used only for local preview clients. When omitted, "
            f"{LOCAL_DEVELOPMENT_MAC} is used with --skip-firewall-sync."
        ),
    )
    parser.add_argument(
        "--skip-firewall-sync",
        action="store_true",
        help="Start the portal without writing nftables rules; intended for tests only.",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = build_config(parse_args())
    app = PortalApplication(config)
    server = ThreadingHTTPServer((config.bind, config.port), app.handler_class())
    LOGGER.info("AP portal listening on %s:%s", config.bind, config.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("AP portal stopping")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
