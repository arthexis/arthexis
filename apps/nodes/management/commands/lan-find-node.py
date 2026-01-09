"""Discover LAN nodes and register reachable peers."""

from __future__ import annotations

import ipaddress
import itertools
import json
import logging
import shutil
import subprocess
from collections.abc import Iterable

import psutil
import requests
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.test import RequestFactory
from requests import RequestException

from apps.nodes.models import Node
from apps.nodes.views import register_node
from config.settings_helpers import discover_local_ip_addresses

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Scan LAN neighbors for ArtHExis nodes and register them as peers."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--ports",
            default="8888,80,443",
            help="Comma-separated port list to probe (default: 8888,80,443).",
        )
        parser.add_argument(
            "--timeout",
            type=float,
            default=2.0,
            help="Timeout in seconds for each HTTP request (default: 2).",
        )
        parser.add_argument(
            "--max-hosts",
            type=int,
            default=256,
            help="Maximum number of hosts to scan per interface (default: 256).",
        )
        parser.add_argument(
            "--interfaces",
            default="eth0,wlan0",
            help="Comma-separated interface list to scan (default: eth0,wlan0).",
        )

    def handle(self, *args, **options):
        ports = self._parse_ports(options["ports"])
        timeout = options["timeout"]
        max_hosts = options["max_hosts"]
        interfaces = self._parse_interfaces(options["interfaces"])

        local_node = Node.get_local()
        local_mac = (local_node.mac_address or "").lower() if local_node else ""
        local_ips = discover_local_ip_addresses()

        candidates: set[str] = set()
        for interface in interfaces:
            candidates.update(self._iter_interface_hosts(interface, max_hosts))
            candidates.update(self._iter_known_interface_hosts(interface))
        candidates.difference_update(local_ips)

        if not candidates:
            self.stdout.write(self.style.WARNING("No candidate hosts discovered."))
            return

        session = requests.Session()
        registered = 0
        seen = 0

        for host in sorted(candidates):
            for port in ports:
                info = self._probe_node_info(session, host, port, timeout=timeout)
                if not info:
                    continue
                seen += 1
                mac_address = (info.get("mac_address") or "").lower()
                if not mac_address:
                    self.stdout.write(
                        self.style.WARNING(
                            f"Skipping {host}:{port} (missing mac_address)."
                        )
                    )
                    continue
                if local_mac and mac_address == local_mac:
                    self.stdout.write(
                        self.style.WARNING(
                            f"Skipping {host}:{port} (local node detected)."
                        )
                    )
                    continue

                payload = self._build_payload(info)
                try:
                    self._register_host_locally(payload)
                except CommandError as exc:
                    self.stdout.write(
                        self.style.WARNING(
                            f"Failed to register {host}:{port}: {exc}"
                        )
                    )
                    continue

                registered += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Registered peer {payload.get('hostname') or host}:{payload.get('port')}"
                    )
                )
                break

        summary = (
            f"Discovery complete. Candidates={len(candidates)} "
            f"Reachable={seen} Registered={registered}"
        )
        self.stdout.write(summary)

    def _parse_ports(self, raw_value: str) -> list[int]:
        ports: list[int] = []
        for token in raw_value.split(","):
            token = token.strip()
            if not token:
                continue
            try:
                port = int(token)
            except ValueError as exc:
                raise CommandError(f"Invalid port: {token}") from exc
            if not 1 <= port <= 65535:
                raise CommandError(f"Port out of range: {port}")
            ports.append(port)
        if not ports:
            raise CommandError("At least one port is required")
        return ports

    def _parse_interfaces(self, raw_value: str) -> list[str]:
        interfaces: list[str] = []
        for token in raw_value.split(","):
            token = token.strip()
            if token:
                interfaces.append(token)
        if not interfaces:
            raise CommandError("At least one interface is required")
        return interfaces

    def _iter_interface_hosts(
        self,
        interface_name: str,
        max_hosts: int,
    ) -> Iterable[str]:
        addresses = psutil.net_if_addrs().get(interface_name)
        if not addresses:
            return

        for addr in addresses:
            if addr.family.name not in ("AF_INET", "AF_INET6"):
                continue
            if not addr.address or not addr.netmask:
                continue
            try:
                interface = ipaddress.ip_interface(
                    f"{addr.address}/{addr.netmask}"
                )
            except ValueError:
                continue
            network = interface.network
            candidates = itertools.islice(network.hosts(), max_hosts)
            for candidate in candidates:
                yield str(candidate)

    def _iter_known_interface_hosts(self, interface_name: str) -> Iterable[str]:
        if interface_name not in psutil.net_if_stats():
            return ()
        ip_path = shutil.which("ip")
        if not ip_path:
            return ()
        try:
            result = subprocess.run(
                [ip_path, "neigh", "show", "dev", interface_name],
                capture_output=True,
                text=True,
                check=False,
                timeout=1.0,
            )
        except (OSError, subprocess.SubprocessError):
            return ()
        if result.returncode != 0:
            return ()
        return (
            token
            for line in result.stdout.splitlines()
            for token in line.split()
            if self._is_ip(token)
        )

    def _is_ip(self, value: str) -> bool:
        try:
            ipaddress.ip_address(value)
            return True
        except ValueError:
            return False

    def _probe_node_info(
        self,
        session: requests.Session,
        host: str,
        port: int,
        *,
        timeout: float,
    ) -> dict | None:
        schemes = self._schemes_for_port(port)
        for scheme in schemes:
            url = f"{scheme}://{host}:{port}/nodes/info/"
            try:
                response = session.get(url, timeout=timeout)
            except RequestException:
                continue
            if response.status_code != 200:
                continue
            try:
                payload = response.json()
            except ValueError:
                logger.debug("Invalid JSON from %s", url)
                continue
            if isinstance(payload, dict) and payload.get("hostname"):
                return payload
        return None

    def _schemes_for_port(self, port: int) -> tuple[str, ...]:
        if port == 443:
            return ("https",)
        if port == 80:
            return ("http",)
        return ("http", "https")

    def _build_payload(self, info: dict) -> dict:
        payload = {
            "hostname": info.get("hostname", ""),
            "address": info.get("address", ""),
            "port": info.get("port", 8888),
            "mac_address": info.get("mac_address", ""),
            "public_key": info.get("public_key", ""),
            "features": info.get("features") or [],
            "trusted": True,
            "current_relation": "Peer",
        }
        for key in (
            "network_hostname",
            "ipv4_address",
            "ipv6_address",
            "installed_version",
            "installed_revision",
            "base_site_domain",
        ):
            value = info.get(key)
            if value:
                payload[key] = value
        role_value = info.get("role") or info.get("role_name")
        if isinstance(role_value, str) and role_value.strip():
            payload["role"] = role_value.strip()
        return payload

    def _register_host_locally(self, payload: dict) -> None:
        User = get_user_model()
        local_user = (
            User.all_objects.filter(is_superuser=True).first()
            if hasattr(User, "all_objects")
            else User.objects.filter(is_superuser=True).first()
        )
        if not local_user:
            raise CommandError("A superuser is required to complete registration")

        factory = RequestFactory()
        request = factory.post(
            "/nodes/register/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        request.user = local_user
        request._cached_user = local_user
        response = register_node(request)
        if response.status_code != 200:
            try:
                detail = json.loads(response.content.decode()).get("detail", "")
            except (json.JSONDecodeError, UnicodeDecodeError):
                detail = response.content.decode(errors="ignore")
            raise CommandError(
                f"Local registration failed with status {response.status_code}: {detail}"
            )
