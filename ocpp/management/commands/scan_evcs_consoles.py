from __future__ import annotations

import concurrent.futures
import json
import re
from contextlib import suppress
from http.client import HTTPConnection
from ipaddress import IPv4Address, IPv4Interface, IPv4Network, ip_address, ip_network
from typing import Iterator, Sequence

from django.core.management.base import BaseCommand, CommandError

from core.models import Reference


class Command(BaseCommand):
    """Scan the local EVCS network for reachable console interfaces."""

    help = (
        "Scan the EVCS subnet configured for eth0 and create header references "
        "for any consoles discovered on port 8900."
    )

    PORT = 8900
    DEFAULT_INTERFACE = IPv4Interface("192.168.129.10/16")
    DEFAULT_TIMEOUT = 1.0
    DEFAULT_WORKERS = 32
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

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--network",
            help=(
                "CIDR network to scan. Defaults to the eth0 network configured "
                "by network-setup.sh"
            ),
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

    def handle(self, *args, **options):
        network = self._parse_network(options.get("network"))
        start = self._parse_host(options.get("start"), network) if options.get("start") else None
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

        hosts = list(self._iter_hosts(network, start=start, end=end, include_self=include_self))
        if not hosts:
            self.stdout.write(self.style.WARNING("No hosts to scan in the selected range."))
            return

        self.stdout.write(f"Scanning {len(hosts)} host(s) in {network}...")

        discoveries = self._discover(hosts, timeout=timeout, workers=workers)

        if not discoveries:
            self.stdout.write(self.style.WARNING("No EVCS consoles discovered."))
            return

        created = 0
        updated = 0
        for serial, host in sorted(discoveries.items()):
            ref, was_created = self._ensure_reference(serial, host)
            if was_created:
                created += 1
                action = "Created"
            else:
                action = "Updated"
                updated += 1
            self.stdout.write(
                self.style.SUCCESS(f"{action} top link for {serial} at http://{host}:{self.PORT}")
            )

        summary = []
        if created:
            summary.append(f"created {created}")
        if updated:
            summary.append(f"updated {updated}")
        summary_text = ", ".join(summary) if summary else "no changes"
        self.stdout.write(f"Discovery complete: {summary_text}.")

    def _parse_network(self, value: str | None) -> IPv4Network:
        if value:
            network = ip_network(value, strict=False)
        else:
            network = self.DEFAULT_INTERFACE.network
        if not isinstance(network, IPv4Network):  # pragma: no cover - defensive programming
            raise CommandError("Only IPv4 networks are supported")
        return network

    def _parse_host(self, value: str, network: IPv4Network) -> IPv4Address:
        host = ip_address(value)
        if not isinstance(host, IPv4Address):  # pragma: no cover - defensive programming
            raise CommandError("Only IPv4 addresses are supported")
        if host not in network:
            raise CommandError(f"Address {value} is not inside {network}")
        return host

    def _iter_hosts(
        self,
        network: IPv4Network,
        *,
        start: IPv4Address | None = None,
        end: IPv4Address | None = None,
        include_self: bool = False,
    ) -> Iterator[IPv4Address]:
        skip = set()
        controller_ip = self.DEFAULT_INTERFACE.ip
        if not include_self and controller_ip in network:
            skip.add(controller_ip)

        for host in network.hosts():
            if start and host < start:
                continue
            if end and host > end:
                break
            if host in skip:
                continue
            yield host

    def _discover(
        self,
        hosts: Sequence[IPv4Address],
        *,
        timeout: float,
        workers: int,
    ) -> dict[str, str]:
        results: dict[str, str] = {}
        if not hosts:
            return results

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(self._probe_host, host, timeout=timeout): host for host in hosts
            }
            for future in concurrent.futures.as_completed(futures):
                try:
                    result = future.result()
                except Exception as exc:  # pragma: no cover - defensive programming
                    host = futures[future]
                    self.stderr.write(f"Error probing {host}: {exc}")
                    continue
                if not result:
                    continue
                serial, host_ip = result
                serial = serial.strip()
                if not serial:
                    continue
                results.setdefault(serial, host_ip)
        return results

    def _probe_host(
        self,
        host: IPv4Address,
        *,
        timeout: float,
    ) -> tuple[str, str] | None:
        host_ip = str(host)
        for path in self.METADATA_PATHS:
            response_body = self._http_get(host_ip, path, timeout=timeout)
            if response_body is None:
                continue
            response, body = response_body
            serial = self._extract_serial(response, body)
            if serial:
                return serial, host_ip
        return None

    def _http_get(
        self, host: str, path: str, *, timeout: float
    ) -> tuple[object, bytes] | None:
        conn = HTTPConnection(host, self.PORT, timeout=timeout)
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

    def _ensure_reference(self, serial: str, host: str) -> tuple[Reference, bool]:
        alt_text = f"{serial} Console"
        url = f"http://{host}:{self.PORT}"
        reference, created = Reference.objects.get_or_create(
            alt_text=alt_text,
            defaults={"value": url, "show_in_header": True, "method": "link"},
        )
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
