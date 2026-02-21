"""Unified node management command with action-based subcommands."""

from __future__ import annotations

import base64
import ipaddress
import itertools
import json
import logging
import socket
import re
import shutil
import subprocess
import textwrap
import uuid
from collections.abc import Iterable
from secrets import token_hex
from urllib.parse import urlsplit, urlunsplit

import psutil
import requests
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.test import RequestFactory
from django.urls import reverse
from requests import RequestException

from apps.nodes.models import Node
from apps.nodes.tasks import poll_peers
from apps.nodes.views import node_info, register_node

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Dispatch node management actions from one Django command."""

    help = (
        "Run node operations via subcommands. "
        "Preferred usage: python manage.py node <action>."
    )
    TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")

    def add_arguments(self, parser):
        """Register action-specific arguments."""

        subparsers = parser.add_subparsers(dest="action")
        subparsers.required = True

        register_parser = subparsers.add_parser(
            "register",
            help="Register this node with a remote host using a CLI token.",
            description="Example: python manage.py node register <base64-token>",
        )
        register_parser.add_argument(
            "token",
            help="Base64 encoded registration token generated from the Nodes admin.",
        )

        register_curl_parser = subparsers.add_parser(
            "register_curl",
            help="Generate a curl-based visitor registration script.",
            description=(
                "Example: python manage.py node register_curl https://host:8888 "
                "--local-base https://localhost:8888"
            ),
        )
        register_curl_parser.add_argument(
            "upstream",
            help="Base URL for the upstream node (e.g. https://host:8888).",
        )
        register_curl_parser.add_argument(
            "--local-base",
            default="https://localhost:8888",
            help="Base URL for the local node (default: https://localhost:8888).",
        )
        register_curl_parser.add_argument(
            "--token",
            default="",
            help="Optional registration token to reuse (default: generate a new token).",
        )

        discover_parser = subparsers.add_parser(
            "discover",
            help="Scan LAN neighbors for Arthexis nodes and register peers.",
            description="Example: python manage.py node discover --interfaces eth0,wlan0",
        )
        discover_parser.add_argument(
            "--ports",
            default="8888,80,443",
            help="Comma-separated port list to probe (default: 8888,80,443).",
        )
        discover_parser.add_argument(
            "--timeout",
            type=float,
            default=2.0,
            help="Timeout in seconds for each HTTP request (default: 2).",
        )
        discover_parser.add_argument(
            "--max-hosts",
            type=int,
            default=256,
            help="Maximum number of hosts to scan per interface (default: 256).",
        )
        discover_parser.add_argument(
            "--interfaces",
            default="eth0,wlan0",
            help="Comma-separated interface list to scan (default: eth0,wlan0).",
        )

        subparsers.add_parser(
            "peers",
            help="Refresh peer node information using the scheduled update workflow.",
            description="Example: python manage.py node peers",
        )
        subparsers.add_parser(
            "check",
            help="Refresh each node and show local and remote update outcomes.",
            description="Example: python manage.py node check",
        )
        subparsers.add_parser(
            "ready",
            help="Verify that this node is ready to register with a host it visits.",
            description="Example: python manage.py node ready",
        )

    def handle(self, *args, **options):
        """Dispatch an action to the corresponding handler."""

        action = options["action"]
        handler = getattr(self, f"_handle_{action}", None)
        if handler is None:
            raise CommandError(f"Unsupported node action: {action}")
        return handler(**options)

    def _handle_register(self, **options):
        payload = self._decode_token(options["token"])
        self._ensure_public_https_url(payload["info"], label="Host info")
        self._ensure_public_https_url(payload["register"], label="Host registration")
        session = requests.Session()
        session.auth = (payload["username"], payload["password"])

        host_info = self._request_json(session, payload["info"])
        visitor_info = self._load_local_info()

        if not host_info.get("base_site_requires_https", False):
            self.stdout.write(
                self.style.WARNING(
                    "Host node is not configured to require HTTPS. Update its Sites settings."
                )
            )
        if not visitor_info.get("base_site_requires_https", False):
            self.stdout.write(
                self.style.WARNING(
                    "Local node is not configured to require HTTPS. Update its Sites settings."
                )
            )

        visitor_payload = self._build_registration_payload(visitor_info, "Downstream")
        visitor_payload["deactivate_user"] = True
        host_result = self._request_json(
            session,
            payload["register"],
            method="post",
            json_body=visitor_payload,
        )
        if not isinstance(host_result, dict) or not host_result.get("id"):
            raise CommandError("Remote registration did not return a node identifier")

        host_payload = self._build_registration_payload(host_info, "Upstream")
        self._register_host_locally(host_payload)

        self.stdout.write(self.style.SUCCESS("Registration completed successfully."))

    def _handle_register_curl(self, **options):
        upstream_base = self._normalize_base_url(options["upstream"], label="Upstream")
        local_base = self._normalize_base_url(options["local_base"], label="Local")
        token = options["token"] or uuid.uuid4().hex
        if not self.TOKEN_PATTERN.match(token):
            raise CommandError(
                "Token must contain only alphanumeric characters, hyphens, or underscores."
            )

        script = textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            set -euo pipefail

            TOKEN=\"{token}\"
            UPSTREAM_INFO=\"{upstream_base}/nodes/info/\"
            UPSTREAM_REGISTER=\"{upstream_base}/nodes/register/\"
            LOCAL_INFO=\"{local_base}/nodes/info/\"
            LOCAL_REGISTER=\"{local_base}/nodes/register/\"

            downstream_payload=\"$(
              curl -fsSL \"${{LOCAL_INFO}}?token=${{TOKEN}}\" | \\
                TOKEN=\"${{TOKEN}}\" RELATION=\"Downstream\" python - <<'PY'
            import json
            import os
            import sys

            data = json.load(sys.stdin)
            token = os.environ[\"TOKEN\"]
            relation = os.environ.get(\"RELATION\")
            signature = data.get(\"token_signature\")
            if not signature:
                raise SystemExit(\"token_signature missing from /nodes/info/\")

            payload = {{
                \"hostname\": data.get(\"hostname\", \"\"),
                \"address\": data.get(\"address\", \"\"),
                \"port\": data.get(\"port\", 8888),
                \"mac_address\": data.get(\"mac_address\", \"\"),
                \"public_key\": data.get(\"public_key\", \"\"),
                \"token\": token,
                \"signature\": signature,
                \"trusted\": True,
            }}
            if relation:
                payload[\"current_relation\"] = relation
            for key in (
                \"network_hostname\",
                \"ipv4_address\",
                \"ipv6_address\",
                \"installed_version\",
                \"installed_revision\",
                \"role\",
                \"base_site_domain\",
            ):
                value = data.get(key)
                if value:
                    payload[key] = value
            if \"features\" in data:
                payload[\"features\"] = data[\"features\"]

            print(json.dumps(payload))
            PY
            )\"

            curl -fsSL -X POST \"${{UPSTREAM_REGISTER}}\" \\
              -H \"Content-Type: application/json\" \\
              -d \"${{downstream_payload}}\"

            upstream_payload=\"$(
              curl -fsSL \"${{UPSTREAM_INFO}}?token=${{TOKEN}}\" | \\
                TOKEN=\"${{TOKEN}}\" RELATION=\"Upstream\" python - <<'PY'
            import json
            import os
            import sys

            data = json.load(sys.stdin)
            token = os.environ[\"TOKEN\"]
            relation = os.environ.get(\"RELATION\")
            signature = data.get(\"token_signature\")
            if not signature:
                raise SystemExit(\"token_signature missing from /nodes/info/\")

            payload = {{
                \"hostname\": data.get(\"hostname\", \"\"),
                \"address\": data.get(\"address\", \"\"),
                \"port\": data.get(\"port\", 8888),
                \"mac_address\": data.get(\"mac_address\", \"\"),
                \"public_key\": data.get(\"public_key\", \"\"),
                \"token\": token,
                \"signature\": signature,
                \"trusted\": True,
            }}
            if relation:
                payload[\"current_relation\"] = relation
            for key in (
                \"network_hostname\",
                \"ipv4_address\",
                \"ipv6_address\",
                \"installed_version\",
                \"installed_revision\",
                \"role\",
                \"base_site_domain\",
            ):
                value = data.get(key)
                if value:
                    payload[key] = value
            if \"features\" in data:
                payload[\"features\"] = data[\"features\"]

            print(json.dumps(payload))
            PY
            )\"

            curl -fsSL -X POST \"${{LOCAL_REGISTER}}\" \\
              -H \"Content-Type: application/json\" \\
              -d \"${{upstream_payload}}\"
            """
        )
        self.stdout.write(script)

    def _handle_discover(self, **options):
        ports = self._parse_ports(options["ports"])
        timeout = options["timeout"]
        max_hosts = options["max_hosts"]
        interfaces = self._parse_interfaces(options["interfaces"])

        local_node = Node.get_local()
        local_mac = (local_node.mac_address or "").lower() if local_node else ""
        local_ips = self._collect_local_ip_addresses()

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

                payload = self._build_discovered_peer_payload(info)
                try:
                    self._register_host_locally(payload)
                except CommandError as exc:
                    self.stdout.write(
                        self.style.WARNING(f"Failed to register {host}:{port}: {exc}")
                    )
                    continue

                registered += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Registered peer {payload.get('hostname') or host}:{payload.get('port')}"
                    )
                )
                break

        self.stdout.write(
            f"Discovery complete. Candidates={len(candidates)} Reachable={seen} Registered={registered}"
        )

    def _handle_peers(self, **options):
        self._report_summary(poll_peers())

    def _handle_check(self, **options):
        self._report_summary(poll_peers(enforce_feature=False))

    def _handle_ready(self, **options):
        self._run_registration_checks()

    def _decode_token(self, token: str) -> dict:
        try:
            payload = json.loads(base64.urlsafe_b64decode(token).decode("utf-8"))
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            raise CommandError("Invalid registration token") from exc

        required = {"register", "info", "username", "password"}
        missing = sorted(required - set(payload))
        if missing:
            raise CommandError(
                f"Registration token missing required fields: {', '.join(missing)}"
            )
        return payload

    def _request_json(
        self,
        session: requests.Session,
        url: str,
        *,
        method: str = "get",
        json_body=None,
    ):
        try:
            response = session.request(method=method, url=url, json=json_body, timeout=10)
        except RequestException as exc:
            raise CommandError(f"Unable to reach {url}: {exc}") from exc

        try:
            data = response.json()
        except ValueError:
            data = {}

        if not response.ok:
            detail = data.get("detail") if isinstance(data, dict) else response.text
            raise CommandError(
                f"Request to {url} failed with status {response.status_code}: {detail}"
            )
        return data

    def _ensure_https_url(self, url: str, *, label: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme != "https":
            raise CommandError(f"{label} URL must use https: {url}")

    def _ensure_public_https_url(self, url: str, *, label: str) -> None:
        self._ensure_https_url(url, label=label)
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").strip().lower()
        if not hostname:
            raise CommandError(f"{label} URL is missing a host: {url}")
        if self._host_is_private_or_local(hostname):
            raise CommandError(
                f"{label} URL host must not resolve to local or private addresses: {url}"
            )

    def _host_is_private_or_local(self, hostname: str) -> bool:
        if hostname in {"localhost", "localhost.localdomain"}:
            return True

        try:
            host_ip = ipaddress.ip_address(hostname)
        except ValueError:
            host_ip = None

        if host_ip is not None:
            return self._is_non_public_ip(host_ip)

        try:
            infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
        except socket.gaierror:
            return False

        for info in infos:
            resolved_host = info[4][0]
            try:
                resolved_ip = ipaddress.ip_address(resolved_host)
            except ValueError:
                continue
            if self._is_non_public_ip(resolved_ip):
                return True
        return False

    def _is_non_public_ip(
        self, value: ipaddress.IPv4Address | ipaddress.IPv6Address
    ) -> bool:
        return any(
            (
                value.is_private,
                value.is_loopback,
                value.is_link_local,
                value.is_multicast,
                value.is_reserved,
                value.is_unspecified,
            )
        )

    def _load_local_info(self) -> dict:
        factory = RequestFactory()
        request = factory.get("/nodes/info/")
        response = node_info(request)
        if response.status_code != 200:
            raise CommandError("Unable to load local node information")
        try:
            return json.loads(response.content.decode())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CommandError("Local node information payload is invalid") from exc

    def _build_registration_payload(self, info: dict, relation: str | None) -> dict:
        payload = {
            "hostname": info.get("hostname", ""),
            "address": info.get("address", ""),
            "port": info.get("port", 8888),
            "mac_address": info.get("mac_address", ""),
            "public_key": info.get("public_key", ""),
            "features": info.get("features") or [],
            "trusted": True,
        }
        if relation:
            payload["current_relation"] = relation
        for key in (
            "network_hostname",
            "ipv4_address",
            "ipv6_address",
            "installed_version",
            "installed_revision",
        ):
            value = info.get(key)
            if value:
                payload[key] = value
        role_value = info.get("role") or info.get("role_name")
        if isinstance(role_value, str) and role_value.strip():
            payload["role"] = role_value.strip()
        return payload

    def _build_discovered_peer_payload(self, info: dict) -> dict:
        payload = self._build_registration_payload(info, "Peer")
        value = info.get("base_site_domain")
        if value:
            payload["base_site_domain"] = value
        return payload

    def _register_host_locally(self, payload: dict) -> None:
        user_model = get_user_model()
        local_user = (
            user_model.all_objects.filter(is_superuser=True).first()
            if hasattr(user_model, "all_objects")
            else user_model.objects.filter(is_superuser=True).first()
        )
        if not local_user:
            raise CommandError("A superuser is required to complete local registration")

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
            except (UnicodeDecodeError, json.JSONDecodeError):
                detail = response.content.decode(errors="ignore")
            raise CommandError(
                f"Local registration failed with status {response.status_code}: {detail}"
            )

    def _normalize_base_url(self, raw: str, *, label: str) -> str:
        if not raw:
            raise CommandError(f"{label} base URL is required.")
        candidate = raw.strip()
        if "://" not in candidate:
            candidate = f"https://{candidate}"
        parsed = urlsplit(candidate)
        if not parsed.scheme or not parsed.netloc:
            raise CommandError(f"{label} base URL is invalid: {raw}")
        if parsed.scheme != "https":
            raise CommandError(f"{label} base URL must use https: {raw}")
        if re.search(r"[^A-Za-z0-9.\-:\[\]]", parsed.netloc):
            raise CommandError(f"{label} base URL contains unsupported characters: {raw}")
        if any((parsed.username, parsed.password, parsed.query, parsed.fragment)):
            raise CommandError(
                f"{label} base URL must not include credentials, query params, or fragments: {raw}"
            )
        if parsed.path and parsed.path not in ("", "/"):
            raise CommandError(f"{label} base URL must not include a path: {raw}")
        if not parsed.hostname:
            raise CommandError(f"{label} base URL is invalid: {raw}")
        return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")

    def _collect_local_ip_addresses(self) -> set[str]:
        addresses: set[str] = {"127.0.0.1", "::1"}
        for interface_addresses in psutil.net_if_addrs().values():
            for addr in interface_addresses:
                if addr.family.name not in ("AF_INET", "AF_INET6"):
                    continue
                if not addr.address:
                    continue
                try:
                    normalized = ipaddress.ip_address(addr.address.split("%", 1)[0])
                except ValueError:
                    continue
                addresses.add(normalized.compressed)
        return addresses

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

    def _iter_interface_hosts(self, interface_name: str, max_hosts: int) -> Iterable[str]:
        addresses = psutil.net_if_addrs().get(interface_name)
        if not addresses:
            return

        for addr in addresses:
            if addr.family.name not in ("AF_INET", "AF_INET6"):
                continue
            if not addr.address or not addr.netmask:
                continue
            try:
                interface = ipaddress.ip_interface(f"{addr.address}/{addr.netmask}")
            except ValueError:
                continue
            candidates = itertools.islice(interface.network.hosts(), max_hosts)
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
        for scheme in self._schemes_for_port(port):
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

    def _run_registration_checks(self) -> None:
        node, created = Node.register_current()
        ready = True

        if created:
            self.stdout.write(
                self.style.SUCCESS(f"Registered current node as {node.hostname}:{node.port}.")
            )
        else:
            self.stdout.write(f"Current node record refreshed ({node.hostname}:{node.port}).")

        security_dir = node.get_base_path() / "security"
        priv_path = security_dir / f"{node.public_endpoint}"
        pub_path = security_dir / f"{node.public_endpoint}.pub"

        missing_files = [path.name for path in (priv_path, pub_path) if not path.exists()]
        if missing_files:
            ready = False
            self.stderr.write(
                self.style.ERROR("Missing security key files: " + ", ".join(sorted(missing_files)))
            )
        else:
            self.stdout.write(self.style.SUCCESS("Security key files are present."))

        if node.public_key:
            self.stdout.write(self.style.SUCCESS("Public key is stored in the database."))
        else:
            ready = False
            self.stderr.write(self.style.ERROR("Public key is not stored in the database."))

        session = requests.Session()
        session.verify = False
        token = token_hex(16)
        base_url = f"https://localhost:{node.port}"
        info_url = f"{base_url}{reverse('node-info')}"

        try:
            info_response = session.get(info_url, params={"token": token}, timeout=5)
        except RequestException as exc:
            ready = False
            self.stderr.write(self.style.ERROR(f"/nodes/info/ request failed: {exc}"))
            info_data = {}
        else:
            if info_response.status_code != 200:
                ready = False
                self.stderr.write(
                    self.style.ERROR(f"/nodes/info/ returned status {info_response.status_code}.")
                )
                info_data = {}
            else:
                self.stdout.write(
                    self.style.SUCCESS("Local /nodes/info/ endpoint responded successfully.")
                )
                try:
                    info_data = info_response.json()
                except ValueError:
                    ready = False
                    self.stderr.write(
                        self.style.ERROR("/nodes/info/ did not return valid JSON data.")
                    )
                    info_data = {}

        if info_data:
            if info_data.get("token_signature"):
                self.stdout.write(self.style.SUCCESS("Token signing is available."))
            else:
                ready = False
                self.stderr.write(
                    self.style.ERROR(
                        "Token signing is unavailable. The private key may be missing or unreadable."
                    )
                )

        register_url = f"{base_url}{reverse('register-node')}"
        try:
            options_response = session.options(
                register_url,
                headers={"Origin": "https://example.com"},
                timeout=5,
            )
        except RequestException as exc:
            ready = False
            self.stderr.write(
                self.style.ERROR(f"CORS preflight for /nodes/register/ failed: {exc}")
            )
        else:
            if (
                options_response.status_code == 200
                and options_response.headers.get("Access-Control-Allow-Origin")
                == "https://example.com"
            ):
                self.stdout.write(
                    self.style.SUCCESS("CORS preflight for /nodes/register/ succeeded.")
                )
            else:
                ready = False
                self.stderr.write(self.style.ERROR("CORS preflight for /nodes/register/ failed."))

        if info_data.get("token_signature"):
            payload = {
                "hostname": info_data.get("hostname"),
                "address": info_data.get("address"),
                "port": info_data.get("port"),
                "mac_address": info_data.get("mac_address"),
                "public_key": info_data.get("public_key"),
                "token": token,
                "signature": info_data.get("token_signature"),
            }
            if "features" in info_data:
                payload["features"] = info_data["features"]

            try:
                register_response = session.post(
                    register_url,
                    json=payload,
                    headers={"Origin": "https://example.com"},
                    timeout=5,
                )
            except RequestException as exc:
                ready = False
                self.stderr.write(
                    self.style.ERROR(f"Signed registration request failed: {exc}")
                )
            else:
                if register_response.status_code == 200:
                    self.stdout.write(
                        self.style.SUCCESS(
                            "Signed registration request completed successfully."
                        )
                    )
                else:
                    ready = False
                    self.stderr.write(
                        self.style.ERROR(
                            "Signed registration request failed with status "
                            f"{register_response.status_code}: "
                            f"{register_response.text}"
                        )
                    )
        else:
            ready = False
            self.stderr.write(
                self.style.ERROR(
                    "Skipping signed registration test because token signing is unavailable."
                )
            )

        if not ready:
            raise CommandError("Visitor registration is not ready. Review the errors above and retry.")

        self.stdout.write(self.style.SUCCESS("Visitor registration checks passed."))

    def _report_summary(self, summary: dict) -> None:
        if summary.get("skipped"):
            raise CommandError(summary.get("reason") or "Node refresh skipped")

        results = summary.get("results") or []
        if not results:
            self.stdout.write(self.style.WARNING("No nodes to refresh."))
            return

        self.stdout.write(self._build_table(results))
        self.stdout.write("")
        self.stdout.write(
            f"Total: {summary.get('total', 0)} "
            f"(success: {summary.get('success', 0)}, "
            f"partial: {summary.get('partial', 0)}, "
            f"error: {summary.get('error', 0)})"
        )

    def _build_table(self, results: list[dict]) -> str:
        headers = ["ID", "Node", "Status", "Local", "Remote"]
        rows: list[list[str]] = []

        for entry in results:
            rows.append(
                [
                    str(entry.get("node_id", "")),
                    str(entry.get("node", "")),
                    str(entry.get("status", "")),
                    self._format_result(entry.get("local")),
                    self._format_result(entry.get("remote")),
                ]
            )

        col_widths = [len(header) for header in headers]
        for row in rows:
            for index, cell in enumerate(row):
                col_widths[index] = max(col_widths[index], len(cell))

        def _render_row(row: list[str]) -> str:
            return " | ".join(cell.ljust(col_widths[idx]) for idx, cell in enumerate(row))

        separator = "-+-".join("-" * width for width in col_widths)
        lines = [_render_row(headers), separator]
        lines.extend(_render_row(row) for row in rows)
        return "\n".join(lines)

    def _format_result(self, result: dict | None) -> str:
        if not isinstance(result, dict):
            return ""

        status = "OK" if result.get("ok") else "ERROR"
        message = result.get("message") or result.get("detail") or ""

        updated_fields = result.get("updated_fields")
        if not message and updated_fields:
            if isinstance(updated_fields, (list, tuple)):
                message = f"updated: {', '.join(updated_fields)}"
            else:
                message = f"updated: {updated_fields}"

        return " ".join(part for part in (status, message) if part).strip()
