from __future__ import annotations

import concurrent.futures
import json
import re
import ssl
import subprocess
from contextlib import suppress
from http.client import HTTPConnection, HTTPSConnection
from ipaddress import IPv4Address, IPv4Interface, IPv4Network, ip_address, ip_interface, ip_network
from typing import Sequence

from django.core.management.base import BaseCommand, CommandError

from core.models import Reference
from ocpp.evcs_discovery import (
    ConsoleEndpoint,
    DEFAULT_CONSOLE_PORT,
    DEFAULT_TOP_PORTS,
    HTTPS_PORTS,
    build_console_url,
    normalise_host,
    prioritise_ports,
    scan_open_ports,
)
from ocpp.reference_utils import host_is_local_loopback


class Command(BaseCommand):
    """Discover EVCS consoles and create header references for them."""

    help = (
        "Use nmap to discover EVCS consoles reachable on the configured interface, "
        "probe their HTTP endpoints to determine the charger serial number and "
        "register a header link pointing at the detected port."
    )

    DEFAULT_INTERFACE = "eth0"
    DEFAULT_TIMEOUT = 1.0
    DEFAULT_WORKERS = 32
    DEFAULT_PORT = DEFAULT_CONSOLE_PORT
    MAX_BODY_BYTES = 8192

    METADATA_PATHS: Sequence[str] = (
        "/api/system/info",
        "/api/system/config",
        "/diagnostics",
        "/status",
        "/",
    )

    JSON_SERIAL_KEYS = {
        "serial",
        "serialnumber",
        "serial_no",
        "serialno",
        "chargepointserialnumber",
        "chargepointid",
        "chargerserialnumber",
        "chargerserial",
    }

    SERIAL_PATTERNS = [
        re.compile(r"charge[_-]?point[_-]?serial[_-]?number\"?\s*[:=]\s*\"?([A-Za-z0-9._-]+)", re.I),
        re.compile(r"charger[_-]?serial[_-]?number\"?\s*[:=]\s*\"?([A-Za-z0-9._-]+)", re.I),
        re.compile(r"serial[_-]?number\"?\s*[:=]\s*\"?([A-Za-z0-9._-]+)", re.I),
        re.compile(r"data-serial=\"([A-Za-z0-9._-]+)\"", re.I),
        re.compile(r"Serial(?:\s*Number)?[:\s]+([A-Za-z0-9._-]+)", re.I),
    ]

    def __init__(self):
        super().__init__()
        self._ssl_context = self._build_ssl_context()

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--interface",
            default=self.DEFAULT_INTERFACE,
            help="Network interface used to discover the EVCS subnet (default: %(default)s)",
        )
        parser.add_argument(
            "--network",
            help="Override the CIDR network to scan (otherwise determined from the interface)",
        )
        parser.add_argument(
            "--start",
            help="First host address to include in the scan (inclusive)",
        )
        parser.add_argument(
            "--end",
            help="Last host address to include in the scan (inclusive)",
        )
        parser.add_argument(
            "--timeout",
            type=float,
            default=self.DEFAULT_TIMEOUT,
            help="Timeout in seconds for each HTTP probe (default: %(default)s)",
        )
        parser.add_argument(
            "--workers",
            type=int,
            default=self.DEFAULT_WORKERS,
            help="Number of worker threads used for probing (default: %(default)s)",
        )
        parser.add_argument(
            "--include-self",
            action="store_true",
            help="Include the controller IP address in the scan",
        )
        parser.add_argument(
            "--full",
            action="store_true",
            help="Perform a full TCP port sweep instead of scanning the top ports",
        )
        parser.add_argument(
            "--top-ports",
            type=int,
            default=DEFAULT_TOP_PORTS,
            help="When not running a full sweep, limit the nmap scan to the top N ports",
        )
        parser.add_argument(
            "--nmap-path",
            default="nmap",
            help="Path to the nmap binary to use",
        )
        parser.add_argument(
            "--ip-path",
            default="ip",
            help="Path to the ip utility used to inspect network interfaces",
        )

    def handle(self, *args, **options):
        interface = options.get("interface") or self.DEFAULT_INTERFACE
        network_option = options.get("network")
        ip_path = options.get("ip_path", "ip")
        nmap_path = options.get("nmap_path", "nmap")

        network, controller_ip = self._resolve_network(interface, network_option, ip_path)

        start = (
            self._parse_host(options.get("start"), network) if options.get("start") else None
        )
        end = self._parse_host(options.get("end"), network) if options.get("end") else None
        if start and end and start > end:
            raise CommandError("--start must not be greater than --end")

        timeout = options.get("timeout", self.DEFAULT_TIMEOUT)
        if timeout <= 0:
            raise CommandError("--timeout must be greater than zero")

        workers = options.get("workers", self.DEFAULT_WORKERS)
        if workers <= 0:
            raise CommandError("--workers must be greater than zero")

        include_self = options.get("include_self", False)
        full_scan = options.get("full", False)
        top_ports = options.get("top_ports", DEFAULT_TOP_PORTS)
        if not full_scan and top_ports <= 0:
            raise CommandError("--top-ports must be greater than zero")

        hosts = self._discover_hosts(
            network,
            include_self=include_self,
            controller_ip=controller_ip,
            nmap_path=nmap_path,
        )

        hosts = self._filter_hosts(hosts, start=start, end=end)

        if not hosts:
            self.stdout.write(self.style.WARNING("No hosts to scan in the selected range."))
            return

        self.stdout.write(f"Scanning {len(hosts)} host(s) in {network} via {interface}...")

        discoveries = self._discover(
            hosts,
            timeout=timeout,
            workers=workers,
            nmap_path=nmap_path,
            full_scan=full_scan,
            top_ports=top_ports,
        )

        if not discoveries:
            self.stdout.write(self.style.WARNING("No EVCS consoles discovered."))
            return

        created = 0
        updated = 0
        for serial, endpoint in sorted(discoveries.items()):
            ensured = self._ensure_reference(serial, endpoint)
            if ensured is None:
                self.stdout.write(
                    self.style.WARNING(
                        f"Skipped top link for {serial} at {endpoint.url} (loopback)",
                    )
                )
                continue
            ref, was_created = ensured
            if was_created:
                created += 1
                action = "Created"
            else:
                action = "Updated"
                updated += 1
            self.stdout.write(self.style.SUCCESS(f"{action} top link for {serial} at {endpoint.url}"))

        summary = []
        if created:
            summary.append(f"created {created}")
        if updated:
            summary.append(f"updated {updated}")
        summary_text = ", ".join(summary) if summary else "no changes"
        self.stdout.write(f"Discovery complete: {summary_text}.")

    def _resolve_network(
        self,
        interface: str,
        network_option: str | None,
        ip_path: str,
    ) -> tuple[IPv4Network, IPv4Address | None]:
        if network_option:
            network = ip_network(network_option, strict=False)
            if not isinstance(network, IPv4Network):
                raise CommandError("Only IPv4 networks are supported")
            controller = self._interface_address(interface, ip_path)
            return network, controller

        iface = self._interface(interface, ip_path)
        return iface.network, iface.ip

    def _interface(self, interface: str, ip_path: str) -> IPv4Interface:
        try:
            proc = subprocess.run(
                [ip_path, "-o", "-4", "addr", "show", "dev", interface],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
        except FileNotFoundError as exc:
            raise CommandError("ip utility is required to discover the interface network") from exc
        except subprocess.SubprocessError as exc:
            raise CommandError(f"Failed to inspect interface {interface}: {exc}") from exc

        if proc.returncode != 0:
            raise CommandError(
                f"Unable to determine IPv4 address for {interface}: exit code {proc.returncode}"
            )

        for line in proc.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) < 4:
                continue
            cidr = parts[3]
            with suppress(ValueError):
                iface = ip_interface(cidr)
                if isinstance(iface, IPv4Interface):
                    return iface
        raise CommandError(f"No IPv4 address configured on {interface}")

    def _interface_address(self, interface: str, ip_path: str) -> IPv4Address | None:
        with suppress(CommandError):
            iface = self._interface(interface, ip_path)
            return iface.ip
        return None

    def _filter_hosts(
        self,
        hosts: Sequence[IPv4Address],
        *,
        start: IPv4Address | None,
        end: IPv4Address | None,
    ) -> list[IPv4Address]:
        filtered: list[IPv4Address] = []
        for host in hosts:
            if start and host < start:
                continue
            if end and host > end:
                continue
            filtered.append(host)
        return filtered

    def _parse_host(self, value: str, network: IPv4Network) -> IPv4Address:
        host = ip_address(value)
        if not isinstance(host, IPv4Address):
            raise CommandError("Only IPv4 addresses are supported")
        if host not in network:
            raise CommandError(f"Address {value} is not inside {network}")
        return host

    def _discover_hosts(
        self,
        network: IPv4Network,
        *,
        include_self: bool,
        controller_ip: IPv4Address | None,
        nmap_path: str,
    ) -> list[IPv4Address]:
        args = [nmap_path, "-sn", "-PR", "-n", str(network), "-oG", "-"]
        try:
            proc = subprocess.run(
                args,
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise CommandError("nmap is required to discover EVCS consoles") from exc
        except subprocess.SubprocessError as exc:
            raise CommandError(f"nmap host discovery failed: {exc}") from exc

        if proc.returncode != 0:
            raise CommandError(f"nmap host discovery failed with exit code {proc.returncode}")

        hosts: list[IPv4Address] = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line or not line.startswith("Host: "):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            candidate = parts[1]
            try:
                address = ip_address(candidate)
            except ValueError:
                continue
            if not isinstance(address, IPv4Address):
                continue
            if not include_self and controller_ip and address == controller_ip:
                continue
            hosts.append(address)
        return hosts

    def _discover(
        self,
        hosts: Sequence[IPv4Address],
        *,
        timeout: float,
        workers: int,
        nmap_path: str,
        full_scan: bool,
        top_ports: int,
    ) -> dict[str, ConsoleEndpoint]:
        results: dict[str, ConsoleEndpoint] = {}
        if not hosts:
            return results

        total_hosts = len(hosts)
        report_interval = max(1, total_hosts // 10)
        completed = 0

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    self._probe_host,
                    host,
                    timeout=timeout,
                    nmap_path=nmap_path,
                    full_scan=full_scan,
                    top_ports=top_ports,
                ): host
                for host in hosts
            }
            for future in concurrent.futures.as_completed(futures):
                host = futures[future]
                try:
                    result = future.result()
                except Exception as exc:  # pragma: no cover - defensive programming
                    self.stderr.write(f"Error probing {host}: {exc}")
                    result = None
                finally:
                    completed += 1
                    if completed % report_interval == 0 or completed == total_hosts:
                        percent = int((completed / total_hosts) * 100)
                        self.stdout.write(
                            f"Scan progress: {completed}/{total_hosts} host(s) checked ({percent}% complete)"
                        )
                if not result:
                    continue
                serial, endpoint = result
                serial = serial.strip()
                if not serial:
                    continue
                if serial not in results:
                    results[serial] = endpoint
        return results

    def _probe_host(
        self,
        host: IPv4Address,
        *,
        timeout: float,
        nmap_path: str,
        full_scan: bool,
        top_ports: int,
    ) -> tuple[str, ConsoleEndpoint] | None:
        host_ip = normalise_host(host)
        ports = scan_open_ports(host_ip, nmap_path=nmap_path, full=full_scan, top_ports=top_ports)
        if ports:
            ports_to_try = prioritise_ports(ports)
        else:
            ports_to_try = prioritise_ports([self.DEFAULT_PORT])

        for port in ports_to_try:
            secure = port in HTTPS_PORTS
            for path in self.METADATA_PATHS:
                response_body = self._http_get(
                    host_ip,
                    port,
                    path,
                    timeout=timeout,
                    secure=secure,
                )
                if response_body is None:
                    continue
                response, body = response_body
                serial = self._extract_serial(response, body)
                if serial:
                    endpoint = ConsoleEndpoint(host=host_ip, port=port, secure=secure)
                    return serial, endpoint
        return None

    def _http_get(
        self,
        host: str,
        port: int,
        path: str,
        *,
        timeout: float,
        secure: bool,
    ) -> tuple[object, bytes] | None:
        connection_cls = HTTPSConnection if secure else HTTPConnection
        kwargs = {"timeout": timeout}
        if secure:
            kwargs["context"] = self._ssl_context
        conn = connection_cls(host, port, **kwargs)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            body = response.read(self.MAX_BODY_BYTES)
            return response, body
        except OSError:
            return None
        finally:
            with suppress(Exception):
                conn.close()

    def _build_ssl_context(self):
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context

    def _extract_serial(self, response, body: bytes) -> str | None:
        if not body:
            return None
        content_type = (response.getheader("Content-Type") or "").lower()
        if "json" in content_type:
            serial = self._serial_from_json(body)
            if serial:
                return serial
        return self._serial_from_text(body)

    def _serial_from_json(self, body: bytes) -> str | None:
        try:
            data = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        return self._search_serial(data)

    def _search_serial(self, data) -> str | None:
        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(value, str) and key.lower() in self.JSON_SERIAL_KEYS:
                    candidate = value.strip()
                    if candidate:
                        return candidate
                serial = self._search_serial(value)
                if serial:
                    return serial
        elif isinstance(data, list):
            for item in data:
                serial = self._search_serial(item)
                if serial:
                    return serial
        return None

    def _serial_from_text(self, body: bytes) -> str | None:
        try:
            text = body.decode("utf-8", errors="ignore")
        except Exception:  # pragma: no cover - defensive programming
            return None
        for pattern in self.SERIAL_PATTERNS:
            match = pattern.search(text)
            if match:
                candidate = match.group(1).strip()
                if candidate:
                    return candidate
        return None

    def _ensure_reference(self, serial: str, endpoint: ConsoleEndpoint) -> tuple[Reference, bool] | None:
        host = endpoint.host
        if host_is_local_loopback(host):
            return None
        alt_text = f"{serial} Console"
        url = build_console_url(host, endpoint.port, endpoint.secure)
        reference = Reference.objects.filter(alt_text=alt_text).order_by("id").first()
        created = False
        if reference is None:
            reference = Reference.objects.create(
                alt_text=alt_text,
                value=url,
                show_in_header=True,
                method="link",
            )
            created = True
        updates: list[str] = []
        if reference.value != url:
            reference.value = url
            updates.append("value")
        if reference.method != "link":
            reference.method = "link"
            updates.append("method")
        if not reference.show_in_header:
            reference.show_in_header = True
            updates.append("show_in_header")
        if updates:
            reference.save(update_fields=updates)
        return reference, created and not updates
