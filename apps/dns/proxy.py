from __future__ import annotations

from dataclasses import dataclass
import logging
import socketserver
import threading
from typing import Iterable

import dns.message
import dns.query
import dns.rcode

logger = logging.getLogger(__name__)


def parse_dns_servers(value: str) -> list[str]:
    if not value:
        return []
    raw = value.replace(",", " ")
    parts = [part.strip() for part in raw.split() if part.strip()]
    return parts


@dataclass(frozen=True)
class DNSProxyRuntimeConfig:
    listen_host: str
    listen_port: int
    upstream_servers: list[str]
    use_tcp: bool = False
    timeout_seconds: float = 2.0


class _ThreadingUDPServer(socketserver.ThreadingUDPServer):
    allow_reuse_address = True


class _ThreadingTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True


class _ProxyMixin:
    def _resolve(self, wire: bytes) -> bytes:
        try:
            request = dns.message.from_wire(wire)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Unable to parse DNS query: %s", exc)
            return b""

        response = None
        for upstream in self.server.upstreams:
            try:
                if self.server.use_tcp:
                    response = dns.query.tcp(
                        request, upstream, timeout=self.server.timeout_seconds
                    )
                else:
                    response = dns.query.udp(
                        request, upstream, timeout=self.server.timeout_seconds
                    )
                break
            except Exception as exc:
                logger.debug("DNS upstream %s failed: %s", upstream, exc)

        if response is None:
            response = dns.message.make_response(request)
            response.set_rcode(dns.rcode.SERVFAIL)

        return response.to_wire()


class DNSUDPProxyHandler(_ProxyMixin, socketserver.BaseRequestHandler):
    def handle(self) -> None:
        data, sock = self.request
        if not data:
            return
        wire = self._resolve(data)
        if not wire:
            return
        sock.sendto(wire, self.client_address)


class DNSTCPProxyHandler(_ProxyMixin, socketserver.StreamRequestHandler):
    def handle(self) -> None:
        length_prefix = self.rfile.read(2)
        if len(length_prefix) != 2:
            return
        length = int.from_bytes(length_prefix, "big")
        if length <= 0:
            return
        data = self.rfile.read(length)
        if not data:
            return
        wire = self._resolve(data)
        if not wire:
            return
        response_length = len(wire).to_bytes(2, "big")
        self.wfile.write(response_length + wire)


class DNSProxyServer:
    def __init__(self, config: DNSProxyRuntimeConfig):
        self.config = config
        self._udp_server = _ThreadingUDPServer(
            (config.listen_host, config.listen_port), DNSUDPProxyHandler
        )
        self._tcp_server = _ThreadingTCPServer(
            (config.listen_host, config.listen_port), DNSTCPProxyHandler
        )
        for server in (self._udp_server, self._tcp_server):
            server.upstreams = list(config.upstream_servers)
            server.use_tcp = config.use_tcp
            server.timeout_seconds = config.timeout_seconds
            server.daemon_threads = True
        self._threads: list[threading.Thread] = []

    def serve_forever(self) -> None:
        if not self.config.upstream_servers:
            raise ValueError("Upstream DNS servers are required to start the proxy.")
        self._threads = [
            threading.Thread(target=self._udp_server.serve_forever, daemon=True),
            threading.Thread(target=self._tcp_server.serve_forever, daemon=True),
        ]
        for thread in self._threads:
            thread.start()
        for thread in self._threads:
            thread.join()

    def shutdown(self) -> None:
        for server in (self._udp_server, self._tcp_server):
            server.shutdown()
            server.server_close()
