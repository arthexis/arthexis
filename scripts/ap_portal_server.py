#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
import re
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import parse_qs, urlparse


BASE_DIR = Path(__file__).resolve().parents[1]
ASSETS_DIR = BASE_DIR / "config" / "data" / "ap_portal"
DEFAULT_STATE_DIR = BASE_DIR / ".state" / "ap_portal"
AUTHORIZED_MACS_PATH = DEFAULT_STATE_DIR / "authorized_macs.txt"
CONSENTS_PATH = DEFAULT_STATE_DIR / "consents.jsonl"
NFT_TABLE_NAME = "arthexis_ap_portal"
AUTHORIZED_SET_NAME = "authorized_macs"
TERMS_VERSION = "qol-recording-v1"
TERMS_STATEMENT = (
    "I accept that my internet experience may be altered and recorded "
    "for quality of life purposes while using this access point."
)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAC_RE = re.compile(r"(?P<mac>([0-9a-f]{2}:){5}[0-9a-f]{2})", re.IGNORECASE)
LOGGER = logging.getLogger("arthexis.ap_portal")
_DJANGO_READY = False


@dataclass(frozen=True)
class PortalConfig:
    bind: str
    port: int
    assets_dir: Path
    state_dir: Path
    authorized_macs_path: Path
    consents_path: Path
    redirect_url: str | None = None


class FirewallSyncError(RuntimeError):
    """Raised when the nftables ruleset cannot be updated."""


def _normalize_mac(value: str) -> str:
    return value.strip().lower()


def _validate_email(value: str) -> str:
    email = value.strip().lower()
    if not EMAIL_RE.match(email):
        raise ValueError("Enter a valid email address.")
    return email


def _normalize_redirect_url(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _ensure_django() -> None:
    global _DJANGO_READY

    if _DJANGO_READY:
        return

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()
    _DJANGO_READY = True


def _read_text(path: Path) -> bytes:
    return path.read_bytes()


class FirewallManager:
    def __init__(self, interface: str = "wlan0") -> None:
        self.interface = interface

    def sync(self, macs: set[str]) -> None:
        subprocess.run(
            ["nft", "delete", "table", "inet", NFT_TABLE_NAME],
            check=False,
            capture_output=True,
            text=True,
        )

        ruleset = self._render_ruleset(sorted(macs))
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


class PortalState:
    def __init__(self, config: PortalConfig) -> None:
        self.config = config
        self._lock = threading.RLock()
        self._firewall = FirewallManager()
        self.config.state_dir.mkdir(parents=True, exist_ok=True)
        self._authorized = self._load_authorized_macs()
        self._firewall.sync(self._authorized)

    def status_for_ip(self, ip_address: str | None) -> dict[str, Any]:
        mac_address = self.resolve_mac(ip_address)
        with self._lock:
            authorized = bool(mac_address and mac_address in self._authorized)
        payload: dict[str, Any] = {
            "authorized": authorized,
            "mac_address": mac_address,
            "terms_version": TERMS_VERSION,
            "terms_statement": TERMS_STATEMENT,
        }
        latest_consent = self._latest_consent_for_mac(mac_address)
        if latest_consent:
            login_mode = str(latest_consent.get("login_mode", "")).strip()
            suite_username = str(latest_consent.get("suite_username", "")).strip()
            if login_mode:
                payload["login_mode"] = login_mode
            if suite_username:
                payload["suite_username"] = suite_username
        if self.config.redirect_url:
            payload["redirect_url"] = self.config.redirect_url
        return payload

    def subscribe(
        self,
        *,
        email: str,
        existing_user: str,
        accept_terms: bool,
        ip_address: str | None,
        user_agent: str,
    ) -> dict[str, Any]:
        if not accept_terms:
            raise ValueError("You must accept the access terms to continue.")

        normalized_email, login_mode, suite_username = self._resolve_identity(
            email=email,
            existing_user=existing_user,
        )
        mac_address = self.resolve_mac(ip_address)
        if not mac_address:
            raise ValueError("Unable to identify this device on the access point yet.")

        record = {
            "accepted_at": datetime.now(timezone.utc).isoformat(),
            "email": normalized_email,
            "accept_terms": True,
            "terms_version": TERMS_VERSION,
            "terms_statement": TERMS_STATEMENT,
            "ip_address": ip_address or "",
            "mac_address": mac_address,
            "login_mode": login_mode,
            "suite_username": suite_username,
            "user_agent": user_agent,
        }

        with self._lock:
            already_authorized = mac_address in self._authorized
            if not already_authorized:
                next_authorized = set(self._authorized)
                next_authorized.add(mac_address)
                self._write_authorized_macs(next_authorized)
                self._firewall.sync(next_authorized)
                self._authorized = next_authorized
            self._append_consent(record)

        payload: dict[str, Any] = {
            "authorized": True,
            "already_authorized": already_authorized,
            "mac_address": mac_address,
            "login_mode": login_mode,
        }
        if suite_username:
            payload["suite_username"] = suite_username
        if self.config.redirect_url:
            payload["redirect_url"] = self.config.redirect_url
        return payload

    def _resolve_identity(
        self, *, email: str, existing_user: str
    ) -> tuple[str, str, str]:
        normalized_email = str(email).strip()
        normalized_existing_user = str(existing_user).strip()

        if normalized_email and normalized_existing_user:
            raise ValueError(
                "Provide either an email address or an existing Arthexis user, not both."
            )
        if normalized_existing_user:
            suite_username = self._lookup_existing_suite_user(normalized_existing_user)
            if not suite_username:
                raise ValueError(
                    "No existing Arthexis user matches that username or email."
                )
            return "", "suite_user", suite_username
        if normalized_email:
            return _validate_email(normalized_email), "email", ""
        raise ValueError("Enter an email address or an existing Arthexis user.")

    def _lookup_existing_suite_user(self, identifier: str) -> str | None:
        try:
            _ensure_django()
            from django.contrib.auth import get_user_model
        except Exception as exc:
            LOGGER.exception("Unable to initialize Django for AP portal user lookup")
            raise ValueError(
                "Unable to verify the Arthexis user right now."
            ) from exc

        UserModel = get_user_model()
        manager = getattr(UserModel, "all_objects", UserModel._default_manager)
        normalized_identifier = str(identifier).strip()
        if not normalized_identifier:
            return None

        user = (
            manager.filter(username__iexact=normalized_identifier, is_active=True)
            .order_by("pk")
            .first()
        )
        if user is None:
            user = (
                manager.filter(email__iexact=normalized_identifier, is_active=True)
                .order_by("pk")
                .first()
            )
        if user is None:
            return None
        return str(user.get_username()).strip()

    def resolve_mac(self, ip_address: str | None) -> str | None:
        if not ip_address:
            return None

        commands = [
            ["ip", "neigh", "show", ip_address],
            ["arp", "-n", ip_address],
        ]
        for command in commands:
            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=2,
                )
            except FileNotFoundError:
                continue
            if result.returncode != 0:
                continue
            match = MAC_RE.search(result.stdout)
            if match:
                return _normalize_mac(match.group("mac"))
        return None

    def _load_authorized_macs(self) -> set[str]:
        if not self.config.authorized_macs_path.exists():
            return set()
        return {
            _normalize_mac(line)
            for line in self.config.authorized_macs_path.read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip()
        }

    def _write_authorized_macs(self, macs: set[str]) -> None:
        lines = sorted(_normalize_mac(mac) for mac in macs if mac)
        payload = "\n".join(lines)
        if payload:
            payload += "\n"
        self.config.authorized_macs_path.write_text(payload, encoding="utf-8")

    def _append_consent(self, record: dict[str, Any]) -> None:
        with self.config.consents_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True))
            handle.write("\n")

    def _latest_consent_for_mac(self, mac_address: str | None) -> dict[str, Any] | None:
        normalized_mac = _normalize_mac(mac_address or "")
        if not normalized_mac or not self.config.consents_path.exists():
            return None

        try:
            lines = self.config.consents_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None

        for raw_line in reversed(lines):
            if not raw_line.strip():
                continue
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if _normalize_mac(str(record.get("mac_address", ""))) == normalized_mac:
                return record
        return None


class PortalApplication:
    def __init__(self, config: PortalConfig) -> None:
        self.config = config
        self.state = PortalState(config)

    def make_handler(self):
        app = self

        class PortalHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                app.handle_get(self)

            def do_POST(self) -> None:  # noqa: N802
                app.handle_post(self)

            def log_message(self, fmt: str, *args: object) -> None:
                LOGGER.info("%s - %s", self.address_string(), fmt % args)

        return PortalHandler

    def handle_get(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urlparse(handler.path)
        path = parsed.path or "/"
        if path == "/health":
            self._send_json(handler, HTTPStatus.OK, {"ok": True})
            return
        if path == "/api/status":
            query = parse_qs(parsed.query)
            ip_address = self._resolve_client_ip(handler, override=query.get("ip", [None])[0])
            self._send_json(handler, HTTPStatus.OK, self.state.status_for_ip(ip_address))
            return
        self._serve_asset(handler, path)

    def handle_post(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urlparse(handler.path)
        if parsed.path != "/api/subscribe":
            self._send_json(handler, HTTPStatus.NOT_FOUND, {"error": "Unknown endpoint."})
            return

        length = int(handler.headers.get("Content-Length", "0") or "0")
        payload = handler.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError:
            self._send_json(
                handler,
                HTTPStatus.BAD_REQUEST,
                {"error": "Invalid JSON payload."},
            )
            return

        try:
            result = self.state.subscribe(
                email=str(data.get("email", "")),
                existing_user=str(data.get("existing_user", "")),
                accept_terms=bool(data.get("accept_terms")),
                ip_address=self._resolve_client_ip(handler),
                user_agent=handler.headers.get("User-Agent", ""),
            )
        except ValueError as exc:
            self._send_json(handler, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        except FirewallSyncError as exc:
            LOGGER.exception("Unable to sync firewall rules")
            self._send_json(
                handler,
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": f"Unable to grant access right now: {exc}"},
            )
            return
        except Exception as exc:  # pragma: no cover
            LOGGER.exception("Unexpected subscribe failure")
            self._send_json(
                handler,
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": f"Unexpected portal failure: {exc}"},
            )
            return

        self._send_json(handler, HTTPStatus.OK, result)

    def _serve_asset(self, handler: BaseHTTPRequestHandler, path: str) -> None:
        relative = path.lstrip("/") or "index.html"
        asset_path = (self.config.assets_dir / relative).resolve()
        if not str(asset_path).startswith(str(self.config.assets_dir.resolve())):
            self._send_json(handler, HTTPStatus.FORBIDDEN, {"error": "Forbidden."})
            return
        if not asset_path.exists() or asset_path.is_dir():
            asset_path = self.config.assets_dir / "index.html"

        try:
            payload = _read_text(asset_path)
        except OSError:
            self._send_json(handler, HTTPStatus.NOT_FOUND, {"error": "Not found."})
            return

        content_type = mimetypes.guess_type(str(asset_path))[0] or "text/plain; charset=utf-8"
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)

    def _resolve_client_ip(
        self, handler: BaseHTTPRequestHandler, *, override: str | None = None
    ) -> str | None:
        candidate = override
        if not candidate:
            forwarded_for = handler.headers.get("X-Forwarded-For", "")
            if forwarded_for:
                candidate = forwarded_for.split(",", 1)[0].strip()
        if not candidate:
            candidate = handler.client_address[0]
        return candidate or None

    def _send_json(
        self, handler: BaseHTTPRequestHandler, status: HTTPStatus, payload: dict[str, Any]
    ) -> None:
        body = json.dumps(payload).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serve the Arthexis access-point consent portal."
    )
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", default=9080, type=int)
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    parser.add_argument(
        "--redirect-url",
        default=os.environ.get("PORTAL_REDIRECT_URL", ""),
        help=(
            "Optional URL to open after access is granted. "
            "Defaults to staying on the local portal."
        ),
    )
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    state_dir = Path(args.state_dir).expanduser().resolve()
    config = PortalConfig(
        bind=args.bind,
        port=args.port,
        assets_dir=ASSETS_DIR,
        state_dir=state_dir,
        authorized_macs_path=state_dir / AUTHORIZED_MACS_PATH.name,
        consents_path=state_dir / CONSENTS_PATH.name,
        redirect_url=_normalize_redirect_url(args.redirect_url),
    )
    app = PortalApplication(config)
    server = ThreadingHTTPServer((config.bind, config.port), app.make_handler())
    LOGGER.info("Starting AP portal on http://%s:%s", config.bind, config.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Stopping AP portal")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
