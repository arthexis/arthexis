"""RFID scanner service and UDP client helpers.

Design note:
The long-running RFID worker intentionally communicates through lock/log files
(``.locks/rfid-scan.json`` and ``logs/rfid-scans.ndjson``). Django processes
ingest those artifacts separately, so this service can run via ``python -m``
without a Django management command invocation.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import socket
import socketserver
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone as datetime_timezone
from pathlib import Path
from typing import Any

import django
from django.conf import settings

from apps.core.notifications import notify_event_async
from apps.screens.startup_notifications import lcd_feature_enabled
from config.loadenv import loadenv
from config.sqlite_driver import bootstrap_sqlite_driver

logger = logging.getLogger(__name__)

SENSITIVE_RFID_KEYS = {"keys", "dump"}

SCAN_STATE_FILE = "rfid-scan.json"
SCAN_LOG_FILE = "rfid-scans.ndjson"
SERVICE_SCAN_LOCKFILE_ERROR = "scan requests are handled via lock-file ingest"


def default_service_host() -> str:
    return os.environ.get("RFID_SERVICE_HOST", "127.0.0.1")


def default_service_port() -> int:
    return int(os.environ.get("RFID_SERVICE_PORT", "29801"))


def default_scan_timeout() -> float:
    return float(os.environ.get("RFID_SERVICE_SCAN_TIMEOUT", "0.3"))


def default_queue_max() -> int:
    return int(os.environ.get("RFID_SERVICE_QUEUE_MAX", "50"))


def default_event_duration() -> int:
    return int(os.environ.get("RFID_EVENT_DURATION", "180"))


def default_scan_dedupe_seconds() -> float:
    return float(os.environ.get("RFID_SCAN_DEDUPE_SECONDS", "1.0"))


DEFAULT_SERVICE_HOST = default_service_host()
DEFAULT_SERVICE_PORT = default_service_port()
DEFAULT_SCAN_TIMEOUT = default_scan_timeout()
DEFAULT_QUEUE_MAX = default_queue_max()
DEFAULT_EVENT_DURATION = default_event_duration()
DEFAULT_SCAN_DEDUPE_SECONDS = default_scan_dedupe_seconds()


def get_next_tag(timeout: float = 0.2) -> dict[str, Any] | None:
    from .background_reader import get_next_tag as background_get_next_tag

    return background_get_next_tag(timeout=timeout)


def is_configured() -> bool:
    from .background_reader import is_configured as background_is_configured

    return background_is_configured()


def start_reader() -> None:
    from .background_reader import start as background_start_reader

    background_start_reader()


def stop_reader() -> None:
    from .background_reader import stop as background_stop_reader

    background_stop_reader()


def toggle_deep_read() -> bool:
    from .reader import toggle_deep_read as reader_toggle_deep_read

    return reader_toggle_deep_read()


@dataclass(frozen=True)
class ServiceEndpoint:
    host: str
    port: int


@dataclass
class ServiceStatus:
    mode: str
    started_at: datetime
    last_scan_at: datetime | None
    queue_depth: int


class ScanQueue:
    def __init__(self, maxlen: int | None = None) -> None:
        queue_maxlen = maxlen if maxlen is not None else default_queue_max()
        self._queue: deque[dict[str, Any]] = deque(maxlen=queue_maxlen)
        self._condition = threading.Condition()
        self._last_scan: dict[str, Any] | None = None
        self._last_scan_at: datetime | None = None

    def put(self, result: dict[str, Any]) -> None:
        with self._condition:
            self._queue.append(result)
            self._last_scan = result
            self._last_scan_at = datetime.now(datetime_timezone.utc)
            self._condition.notify_all()

    def get(self, timeout: float | None = None) -> dict[str, Any] | None:
        with self._condition:
            if not self._queue:
                if timeout and timeout > 0:
                    self._condition.wait(timeout)
            if self._queue:
                return self._queue.popleft()
        return None

    def status(self) -> tuple[int, dict[str, Any] | None, datetime | None]:
        with self._condition:
            return len(self._queue), self._last_scan, self._last_scan_at


class RFIDServiceState:
    def __init__(self) -> None:
        self.queue = ScanQueue()
        self.started_at = datetime.now(datetime_timezone.utc)
        self.stop_event = threading.Event()
        self.worker_thread: threading.Thread | None = None
        self._last_emitted_rfid: str | None = None
        self._last_emitted_at: float | None = None

    def start_worker(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            return
        self.stop_event.clear()
        self.worker_thread = threading.Thread(
            target=self._worker,
            name="rfid-service-worker",
            daemon=True,
        )
        self.worker_thread.start()

    def stop_worker(self) -> None:
        self.stop_event.set()
        if self.worker_thread:
            self.worker_thread.join(timeout=2)

    def _worker(self) -> None:  # pragma: no cover - background loop
        logger.info("RFID service worker starting")
        start_reader()
        try:
            while not self.stop_event.is_set():
                result = get_next_tag(timeout=0.2)
                if not result:
                    continue
                if result.get("error") or result.get("rfid"):
                    logger.debug(
                        "RFID service queued scan result: %s",
                        sanitize_rfid_payload(result),
                    )
                    self.queue.put(result)
                    self._notify_lcd_event(result)
                    self._emit_scan_artifacts(result)
        finally:
            stop_reader()
            logger.info("RFID service worker stopped")

    def _notify_lcd_event(self, result: dict[str, Any]) -> None:
        if not result.get("rfid"):
            return
        base_dir = Path(settings.BASE_DIR)
        lock_dir = base_dir / ".locks"
        if not lcd_feature_enabled(lock_dir):
            return
        label = result.get("label_id")
        allowed = result.get("allowed")
        status_text = "OK" if allowed else "BAD" if allowed is not None else ""
        subject = "RFID"
        if label:
            subject = f"RFID {label} {status_text}".strip()
        elif status_text:
            subject = f"RFID {status_text}".strip()
        rfid_value = str(result.get("rfid", "")).strip()
        color = str(result.get("color", "")).strip()
        body = " ".join(part for part in (rfid_value, color) if part)
        notify_event_async(subject, body, duration=default_event_duration(), event_id=0)

    def _emit_scan_artifacts(self, result: dict[str, Any]) -> None:
        rfid_value = str(result.get("rfid", "") or "").strip().upper()
        if not rfid_value:
            return
        now = time.monotonic()
        if (
            self._last_emitted_rfid == rfid_value
            and self._last_emitted_at is not None
            and now - self._last_emitted_at < default_scan_dedupe_seconds()
        ):
            return
        payload = dict(result)
        payload.setdefault("service_mode", "service")
        payload["scanned_at"] = datetime.now(datetime_timezone.utc).isoformat()
        try:
            write_rfid_scan_lock(payload)
            append_scan_log(payload)
        except Exception:  # pragma: no cover - defensive guard for worker loop
            logger.exception(
                "Failed to emit RFID scan artifacts for rfid=%s payload=%s",
                rfid_value,
                sanitize_rfid_payload(payload),
            )
            return
        self._last_emitted_rfid = rfid_value
        self._last_emitted_at = now

    def status(self) -> ServiceStatus:
        queue_depth, _last_scan, last_scan_at = self.queue.status()
        return ServiceStatus(
            mode="service",
            started_at=self.started_at,
            last_scan_at=last_scan_at,
            queue_depth=queue_depth,
        )


class RFIDServiceHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        data = self.request[0]
        socket_out = self.request[1]
        response: dict[str, Any]
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            logger.debug("RFID service received invalid payload")
            response = {"error": "invalid request", "service_mode": "service"}
            socket_out.sendto(json.dumps(response).encode("utf-8"), self.client_address)
            return

        if not isinstance(payload, dict):
            logger.debug(
                "RFID service received non-dict payload: %s", type(payload).__name__
            )
            response = {"error": "invalid request", "service_mode": "service"}
            socket_out.sendto(json.dumps(response).encode("utf-8"), self.client_address)
            return

        action = str(payload.get("action") or "scan")
        logger.debug(
            "RFID service received action=%s payload=%s",
            action,
            sanitize_rfid_payload(payload),
        )
        state: RFIDServiceState = self.server.state
        if action == "ping":
            status = state.status()
            response = {
                "status": "ok",
                "service_mode": status.mode,
                "started_at": status.started_at.isoformat(),
                "queue_depth": status.queue_depth,
                "last_scan_at": status.last_scan_at.isoformat()
                if status.last_scan_at
                else None,
            }
            socket_out.sendto(json.dumps(response).encode("utf-8"), self.client_address)
            return

        if not is_configured():
            logger.debug("RFID service scan requested but no scanner configured")
            response = {"error": "no scanner available", "service_mode": "service"}
            socket_out.sendto(json.dumps(response).encode("utf-8"), self.client_address)
            return

        if action == "deep_read":
            enabled = toggle_deep_read()
            response = {
                "status": "deep read enabled" if enabled else "deep read disabled",
                "enabled": enabled,
                "service_mode": "service",
            }
            if enabled:
                tag = state.queue.get(timeout=default_scan_timeout())
                if tag is None:
                    tag = get_next_tag(timeout=default_scan_timeout()) or None
                if tag:
                    response["scan"] = tag
                logger.debug(
                    "RFID service deep read response: %s",
                    sanitize_rfid_payload(response),
                )
            socket_out.sendto(json.dumps(response).encode("utf-8"), self.client_address)
            return

        if action == "scan":
            response = {
                "error": SERVICE_SCAN_LOCKFILE_ERROR,
                "service_mode": "service",
            }
            socket_out.sendto(json.dumps(response).encode("utf-8"), self.client_address)
            return

        timeout = payload.get("timeout")
        try:
            timeout_value = float(timeout) if timeout is not None else default_scan_timeout()
        except (TypeError, ValueError):
            timeout_value = default_scan_timeout()

        tag = state.queue.get(timeout=timeout_value)
        if tag is None:
            tag = {"rfid": None, "label_id": None}
            logger.debug("RFID service scan timed out after %.2fs", timeout_value)
        tag["service_mode"] = "service"
        socket_out.sendto(json.dumps(tag).encode("utf-8"), self.client_address)


class RFIDUDPServer(socketserver.ThreadingUDPServer):
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], handler_class):
        super().__init__(server_address, handler_class)
        self.state = RFIDServiceState()


class RFIDServiceRunner:
    def __init__(self, host: str, port: int) -> None:
        self.endpoint = ServiceEndpoint(host=host, port=port)
        self.server = RFIDUDPServer((host, port), RFIDServiceHandler)

    def serve(self) -> None:
        logger.info(
            "RFID service listening on %s:%s", self.endpoint.host, self.endpoint.port
        )
        self.server.state.start_worker()
        try:
            self.server.serve_forever(poll_interval=0.5)
        finally:
            self.server.shutdown()
            self.server.server_close()
            self.server.state.stop_worker()

    def shutdown(self) -> None:
        self.server.shutdown()


def get_lock_dir(base_dir: Path | None = None) -> Path:
    base_dir = base_dir or Path(settings.BASE_DIR)
    return Path(base_dir) / ".locks"


def sanitize_rfid_payload(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in payload.items():
        if key in SENSITIVE_RFID_KEYS:
            sanitized[key] = "[redacted]"
            continue
        if key == "rfid":
            sanitized[key] = mask_rfid(value)
            continue
        if key == "scan" and isinstance(value, dict):
            sanitized[key] = sanitize_rfid_payload(value)
            continue
        sanitized[key] = value
    return sanitized


def mask_rfid(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    if len(text) <= 4:
        return "*" * len(text)
    return f"{'*' * (len(text) - 4)}{text[-4:]}"


def rfid_service_lock_path(base_dir: Path | None = None) -> Path:
    return get_lock_dir(base_dir) / "rfid-service.lck"


def rfid_scan_lock_path(base_dir: Path | None = None) -> Path:
    return get_lock_dir(base_dir) / SCAN_STATE_FILE


def rfid_scan_log_path(base_dir: Path | None = None) -> Path:
    base_dir = base_dir or Path(settings.BASE_DIR)
    log_dir = Path(settings.LOG_DIR)
    if not log_dir.is_absolute():
        log_dir = base_dir / log_dir
    return log_dir / SCAN_LOG_FILE


def write_rfid_scan_lock(payload: dict[str, Any], *, base_dir: Path | None = None) -> None:
    lock_path = rfid_scan_lock_path(base_dir)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def append_scan_log(payload: dict[str, Any], *, base_dir: Path | None = None) -> None:
    log_path = rfid_scan_log_path(base_dir)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(payload, sort_keys=True))
        log_file.write("\n")


def rfid_service_enabled(lock_dir: Path | None = None) -> bool:
    lock_dir = lock_dir or get_lock_dir()
    return (lock_dir / "rfid-service.lck").exists()


def service_endpoint() -> ServiceEndpoint:
    return ServiceEndpoint(host=default_service_host(), port=default_service_port())


def request_service(
    action: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 0.5,
) -> dict[str, Any] | None:
    endpoint = service_endpoint()
    data = {"action": action}
    if payload:
        data.update(payload)
    message = json.dumps(data).encode("utf-8")
    response = None
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.sendto(message, (endpoint.host, endpoint.port))
            resp_bytes, _addr = sock.recvfrom(65535)
            response = json.loads(resp_bytes.decode("utf-8"))
        except (socket.timeout, OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None
    if not isinstance(response, dict):
        return None
    return response


def deep_read_via_service() -> dict[str, Any] | None:
    return request_service("deep_read", timeout=default_scan_timeout())


def service_available(timeout: float = 0.2) -> bool:
    response = request_service("ping", timeout=timeout)
    return bool(response and response.get("status") == "ok")


def run_service(host: str | None = None, port: int | None = None) -> None:
    endpoint = service_endpoint()
    server_host = host or endpoint.host
    server_port = port or endpoint.port
    runner = RFIDServiceRunner(server_host, server_port)

    def _handle_signal(signum, frame) -> None:  # pragma: no cover - signal handling
        logger.info("RFID service received shutdown signal %s", signum)
        shutdown_thread = threading.Thread(
            target=runner.shutdown,
            name="rfid-service-shutdown",
            daemon=True,
        )
        shutdown_thread.start()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    runner.serve()


def main() -> None:
    """Run the RFID UDP service as a module entrypoint."""

    loadenv()
    bootstrap_sqlite_driver()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    django.setup()
    endpoint = service_endpoint()
    parser = argparse.ArgumentParser(description="Run the Arthexis RFID scanner UDP service.")
    parser.add_argument("--host", default=endpoint.host, help="Host interface to bind.")
    parser.add_argument("--port", type=int, default=endpoint.port, help="UDP port to bind.")
    options = parser.parse_args()
    run_service(host=options.host, port=options.port)


if __name__ == "__main__":
    main()
