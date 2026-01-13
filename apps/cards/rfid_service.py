"""RFID scanner service and UDP client helpers."""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import socketserver
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone as datetime_timezone
from pathlib import Path
from typing import Any

from django.conf import settings

from apps.core.notifications import notify_event_async
from apps.screens.startup_notifications import lcd_feature_enabled

from .background_reader import get_next_tag, is_configured, start as start_reader, stop as stop_reader
from .reader import toggle_deep_read

logger = logging.getLogger(__name__)

DEFAULT_SERVICE_HOST = os.environ.get("RFID_SERVICE_HOST", "127.0.0.1")
DEFAULT_SERVICE_PORT = int(os.environ.get("RFID_SERVICE_PORT", "29801"))
DEFAULT_SCAN_TIMEOUT = float(os.environ.get("RFID_SERVICE_SCAN_TIMEOUT", "0.3"))
DEFAULT_QUEUE_MAX = int(os.environ.get("RFID_SERVICE_QUEUE_MAX", "50"))
DEFAULT_EVENT_DURATION = int(os.environ.get("RFID_EVENT_DURATION", "30"))


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
    def __init__(self, maxlen: int = DEFAULT_QUEUE_MAX) -> None:
        self._queue: deque[dict[str, Any]] = deque(maxlen=maxlen)
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
                    self.queue.put(result)
                    self._notify_lcd_event(result)
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
        notify_event_async(subject, body, duration=DEFAULT_EVENT_DURATION)

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
            response = {"error": "invalid request", "service_mode": "service"}
            socket_out.sendto(json.dumps(response).encode("utf-8"), self.client_address)
            return

        if not isinstance(payload, dict):
            response = {"error": "invalid request", "service_mode": "service"}
            socket_out.sendto(json.dumps(response).encode("utf-8"), self.client_address)
            return

        action = str(payload.get("action") or "scan")
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
                tag = state.queue.get(timeout=DEFAULT_SCAN_TIMEOUT)
                if tag is None:
                    tag = get_next_tag(timeout=DEFAULT_SCAN_TIMEOUT) or None
                if tag:
                    response["scan"] = tag
            socket_out.sendto(json.dumps(response).encode("utf-8"), self.client_address)
            return

        timeout = payload.get("timeout")
        try:
            timeout_value = float(timeout) if timeout is not None else DEFAULT_SCAN_TIMEOUT
        except (TypeError, ValueError):
            timeout_value = DEFAULT_SCAN_TIMEOUT

        tag = state.queue.get(timeout=timeout_value)
        if tag is None:
            tag = {"rfid": None, "label_id": None}
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


def rfid_service_lock_path(base_dir: Path | None = None) -> Path:
    return get_lock_dir(base_dir) / "rfid-service.lck"


def rfid_service_enabled(lock_dir: Path | None = None) -> bool:
    lock_dir = lock_dir or get_lock_dir()
    return (lock_dir / "rfid-service.lck").exists()


def service_endpoint() -> ServiceEndpoint:
    return ServiceEndpoint(host=DEFAULT_SERVICE_HOST, port=DEFAULT_SERVICE_PORT)


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


def scan_via_service(timeout: float | None = None) -> dict[str, Any] | None:
    payload: dict[str, Any] = {}
    if timeout is not None:
        payload["timeout"] = timeout
    return request_service("scan", payload, timeout=timeout or DEFAULT_SCAN_TIMEOUT)


def deep_read_via_service() -> dict[str, Any] | None:
    return request_service("deep_read", timeout=DEFAULT_SCAN_TIMEOUT)


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
        runner.shutdown()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    runner.serve()
