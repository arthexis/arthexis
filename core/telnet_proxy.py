"""Telnet proxy runtime management utilities."""

from __future__ import annotations

import logging
import select
import socket
import socketserver
import threading
from contextlib import ExitStack, suppress
from dataclasses import dataclass
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Optional


class _ForwardingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True


class _ForwardingHandler(socketserver.BaseRequestHandler):
    buffer_size = 4096

    def setup(self):  # pragma: no cover - socketserver hook, trivial
        super().setup()
        self._connections: list[socket.socket] = []

    def handle(self):
        server: "_ForwardingTCPServer" = self.server
        log = getattr(server, "logger", None)
        try:
            upstream = socket.create_connection(
                (server.upstream_host, server.upstream_port), timeout=10
            )
        except OSError as exc:  # pragma: no cover - connection failures logged
            if log:
                log.error("Unable to connect to %s:%s: %s", server.upstream_host, server.upstream_port, exc)
            return

        with ExitStack() as stack:
            stack.enter_context(upstream)
            stack.callback(upstream.close)
            self._connections = [self.request, upstream]
            directions = {
                self.request: "client→server",
                upstream: "server→client",
            }

            while True:
                readable, _, exceptional = select.select(self._connections, [], self._connections)
                if exceptional:
                    break
                for conn in readable:
                    try:
                        data = conn.recv(self.buffer_size)
                    except OSError:
                        data = b""
                    if not data:
                        return
                    target = upstream if conn is self.request else self.request
                    try:
                        target.sendall(data)
                    except OSError:
                        return
                    if log:
                        text = data.decode("utf-8", "replace")
                        log.info("%s %s", directions[conn], text)

    def finish(self):  # pragma: no cover - socketserver hook, trivial
        try:
            for conn in self._connections:
                with suppress(OSError):
                    conn.shutdown(socket.SHUT_RDWR)
                    conn.close()
        finally:
            self._connections = []
            super().finish()


@dataclass(frozen=True)
class _ProxyConfig:
    endpoint_host: str
    endpoint_port: int
    upstream_host: str
    upstream_port: int
    log_path: str


class TelnetProxyServer:
    """Runtime representation for a configured telnet proxy."""

    def __init__(self, config: _ProxyConfig):
        self.config = config
        try:
            self._server = _ForwardingTCPServer(
                (config.endpoint_host, config.endpoint_port), _ForwardingHandler
            )
        except OSError:
            raise
        self._server.upstream_host = config.upstream_host
        self._server.upstream_port = config.upstream_port
        logger, handler = self._build_logger(config.log_path)
        self._server.logger = logger
        self._server.log_handler = handler
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="TelnetProxy", daemon=True
        )
        self._is_running = False

    @staticmethod
    def _build_logger(path: str | None) -> tuple[Optional[logging.Logger], Optional[logging.Handler]]:
        if not path:
            return None, None
        log_path = Path(path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger(f"core.telnet_proxy.{log_path.stem}.{id(log_path)}")
        logger.setLevel(logging.INFO)
        handler = TimedRotatingFileHandler(log_path, when="midnight", backupCount=7)
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        logger.handlers = []
        logger.addHandler(handler)
        logger.propagate = False
        return logger, handler

    @property
    def server(self) -> _ForwardingTCPServer:
        return self._server

    @property
    def listening_port(self) -> int:
        return self._server.server_address[1]

    def start(self) -> None:
        if self._is_running:
            return
        self._thread.start()
        self._is_running = True

    def stop(self) -> None:
        if not self._is_running:
            return
        self._server.shutdown()
        self._server.server_close()
        logger = getattr(self._server, "logger", None)
        handler = getattr(self._server, "log_handler", None)
        if logger and handler:
            logger.removeHandler(handler)
            handler.close()
        self._is_running = False

    @property
    def is_running(self) -> bool:
        return self._is_running


_registry: dict[int, TelnetProxyServer] = {}
_lock = threading.Lock()


def _snapshot(model) -> _ProxyConfig:
    return _ProxyConfig(
        endpoint_host=model.endpoint_host,
        endpoint_port=model.endpoint_port,
        upstream_host=model.telnet_host,
        upstream_port=model.telnet_port,
        log_path=model.logfile,
    )


def start_proxy(model) -> TelnetProxyServer:
    """Start a proxy for ``model`` if needed."""

    config = _snapshot(model)
    with _lock:
        runner = _registry.get(model.pk)
        if runner and runner.is_running and runner.config == config:
            return runner
        if runner:
            runner.stop()
        runner = TelnetProxyServer(config)
        runner.start()
        _registry[model.pk] = runner
        return runner


def stop_proxy(model) -> None:
    """Stop the proxy associated with ``model`` if running."""

    with _lock:
        runner = _registry.pop(model.pk, None)
    if runner:
        runner.stop()


def is_proxy_running(model) -> bool:
    runner = get_proxy_runner(model)
    return bool(runner and runner.is_running)


def get_proxy_runner(model) -> Optional[TelnetProxyServer]:
    with _lock:
        return _registry.get(model.pk)


def stop_all_proxies() -> None:
    with _lock:
        runners = list(_registry.items())
        _registry.clear()
    for _pk, runner in runners:
        runner.stop()
