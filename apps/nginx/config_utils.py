from __future__ import annotations

import ipaddress
import re
import subprocess
import tempfile
import textwrap
from pathlib import Path
from typing import Iterable

from django.conf import settings

DEFAULT_CERT_DIR = Path("/etc/letsencrypt/live/arthexis.com")
CERTIFICATE_PATH = DEFAULT_CERT_DIR / "fullchain.pem"
CERTIFICATE_KEY_PATH = DEFAULT_CERT_DIR / "privkey.pem"
SSL_OPTIONS_PATH = Path("/etc/letsencrypt/options-ssl-nginx.conf")
SSL_DHPARAM_PATH = Path("/etc/letsencrypt/ssl-dhparams.pem")
BUNDLED_SSL_OPTIONS_PATH = Path(__file__).with_name("options-ssl-nginx.conf")
BUNDLED_SSL_DHPARAM_PATH = Path(__file__).with_name("ssl-dhparams.pem")


def slugify(domain: str) -> str:
    """Return a filesystem-friendly slug for *domain*."""

    slug = re.sub(r"[^a-z0-9]+", "-", domain.lower()).strip("-")
    return slug or "site"


def default_certificate_domain(hosts: Iterable[str]) -> str:
    candidates: list[str] = []
    for host in hosts or []:
        normalized = str(host or "").strip()
        if not normalized or normalized.startswith("."):
            continue
        if "/" in normalized:
            continue
        if normalized.startswith("[") and "]" in normalized:
            normalized = normalized.split("]", 1)[0].lstrip("[")
        elif ":" in normalized and normalized.count(":") == 1:
            normalized = normalized.rsplit(":", 1)[0]
        if not normalized:
            continue
        try:
            ipaddress.ip_address(normalized)
        except ValueError:
            candidates.append(normalized)
        else:
            continue

    for candidate in candidates:
        if "." in candidate:
            return candidate

    if candidates:
        return candidates[0]

    return "localhost"


def default_certificate_domain_from_settings(settings_obj=settings) -> str:
    hosts = getattr(settings_obj, "ALLOWED_HOSTS", []) or []
    return default_certificate_domain(hosts)


WEBSOCKET_MAP_DIRECTIVE = "map $http_upgrade $connection_upgrade {"
WEBSOCKET_UPGRADE_HEADER = "proxy_set_header Upgrade $http_upgrade;"
WEBSOCKET_CONNECTION_HEADER = "proxy_set_header Connection $connection_upgrade;"
WEBSOCKET_READ_TIMEOUT = "proxy_read_timeout 1d;"
WEBSOCKET_SEND_TIMEOUT = "proxy_send_timeout 1d;"


def websocket_map() -> str:
    return textwrap.dedent(
        """
        map $http_upgrade $connection_upgrade {
            default upgrade;
            '' close;
        }
        """
    ).strip()


def websocket_directives() -> tuple[str, ...]:
    return (
        WEBSOCKET_MAP_DIRECTIVE,
        WEBSOCKET_UPGRADE_HEADER,
        WEBSOCKET_CONNECTION_HEADER,
        WEBSOCKET_READ_TIMEOUT,
        WEBSOCKET_SEND_TIMEOUT,
    )


def proxy_block(
    port: int | None = None,
    *,
    trailing_slash: bool = True,
    external_websockets: bool = True,
    proxy_target: str | None = None,
) -> str:
    """Return the proxy pass configuration block for *port* or *proxy_target*."""

    if proxy_target is None and port is None:
        raise ValueError("proxy_block requires a port or proxy_target")

    upstream = proxy_target or f"127.0.0.1:{port}"
    proxy_pass_target = f"http://{upstream}"
    if trailing_slash:
        proxy_pass_target += "/"

    if external_websockets:
        websocket_lines = textwrap.dedent(
            f"""
            {WEBSOCKET_UPGRADE_HEADER}
            {WEBSOCKET_CONNECTION_HEADER}
            {WEBSOCKET_READ_TIMEOUT}
            {WEBSOCKET_SEND_TIMEOUT}
            """
        ).strip()
    else:
        websocket_lines = textwrap.dedent(
            f"""
            {WEBSOCKET_UPGRADE_HEADER}
            proxy_set_header Connection \"upgrade\";
            """
        ).strip()

    return "\n".join(
        [
            textwrap.dedent(
                f"""
        location / {{
            set $simulator_redirect \"\";
            if ($server_port = 8900) {{
                set $simulator_redirect $uri;
            }}
            if ($simulator_redirect = \"/\") {{
                return 302 /ocpp/evcs/simulator/;
            }}
            proxy_pass {proxy_pass_target};
            proxy_intercept_errors on;
            proxy_http_version 1.1;
        """
            ).strip(),
            textwrap.indent(websocket_lines, "    "),
            textwrap.dedent(
                """
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Host $host;
            proxy_set_header X-Forwarded-Proto $scheme;
        }
                """
            ).strip(),
        ]
    )


def _format_server_block(lines: Iterable[str]) -> str:
    return "\n".join(lines)


def _unique_preserve_order(values: Iterable[str]) -> list[str]:
    """Return *values* with duplicates removed while preserving order."""

    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def _resolve_ssl_asset_path(*candidates: Path) -> Path | None:
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate
    return None


def _ssl_configuration_lines(cert_path: str, key_path: str) -> list[str]:
    lines = [
        f"    ssl_certificate {cert_path};",
        f"    ssl_certificate_key {key_path};",
    ]
    options_path = _resolve_ssl_asset_path(SSL_OPTIONS_PATH, BUNDLED_SSL_OPTIONS_PATH)
    if options_path:
        lines.append(f"    include {options_path};")

    dhparam_path = _resolve_ssl_asset_path(SSL_DHPARAM_PATH, BUNDLED_SSL_DHPARAM_PATH)
    if dhparam_path:
        lines.append(f"    ssl_dhparam {dhparam_path};")

    return lines


def http_proxy_server(
    server_names: str,
    port: int,
    listens: Iterable[str] | None = None,
    *,
    trailing_slash: bool = True,
    external_websockets: bool = True,
    proxy_target: str | None = None,
) -> str:
    """Return an HTTP proxy server block for *server_names*."""

    if listens is None:
        listens = ("80",)

    lines: list[str] = ["server {"]
    for listen in _unique_preserve_order(listens):
        lines.append(f"    listen {listen};")
    lines.append(f"    server_name {server_names};")
    lines.append("")
    lines.append(
        textwrap.indent(
            proxy_block(
                port,
                trailing_slash=trailing_slash,
                external_websockets=external_websockets,
                proxy_target=proxy_target,
            ),
            "    ",
        )
    )
    lines.append("}")
    return _format_server_block(lines)


def http_redirect_server(server_names: str, listens: Iterable[str] | None = None) -> str:
    """Return an HTTP redirect server block for *server_names*."""

    if listens is None:
        listens = ("80",)

    lines: list[str] = ["server {"]
    for listen in _unique_preserve_order(listens):
        lines.append(f"    listen {listen};")
    lines.append(f"    server_name {server_names};")
    lines.append("    return 301 https://$host$request_uri;")
    lines.append("}")
    return _format_server_block(lines)


def default_reject_server(
    listens: Iterable[str] | None = None,
    *,
    https: bool = False,
    certificate_path: str | Path | None = None,
    certificate_key_path: str | Path | None = None,
) -> str:
    """Return a default server block that drops requests for unknown hosts."""

    if listens is None:
        listens = ("80",)

    lines: list[str] = ["server {"]
    for listen in _unique_preserve_order(listens):
        suffix = " default_server" if "default_server" not in listen else ""
        lines.append(f"    listen {listen}{suffix};")
    lines.append("    server_name _;")

    if https:
        cert_path = str(certificate_path or CERTIFICATE_PATH)
        key_path = str(certificate_key_path or CERTIFICATE_KEY_PATH)
        lines.append("")
        lines.extend(_ssl_configuration_lines(cert_path, key_path))

    lines.extend(["", "    return 444;", "}"])
    return _format_server_block(lines)


def https_proxy_server(
    server_names: str,
    port: int,
    listens: Iterable[str] | None = None,
    *,
    certificate_path: str | Path | None = None,
    certificate_key_path: str | Path | None = None,
    trailing_slash: bool = True,
    external_websockets: bool = True,
    proxy_target: str | None = None,
) -> str:
    """Return an HTTPS proxy server block for *server_names*."""

    if listens is None:
        listens = ("443 ssl",)

    lines: list[str] = ["server {"]
    for listen in _unique_preserve_order(listens):
        lines.append(f"    listen {listen};")
    lines.append(f"    server_name {server_names};")
    cert_path = str(certificate_path or CERTIFICATE_PATH)
    key_path = str(certificate_key_path or CERTIFICATE_KEY_PATH)
    lines.extend(_ssl_configuration_lines(cert_path, key_path))
    lines.append(
        "    add_header Content-Security-Policy "
        '"upgrade-insecure-requests; block-all-mixed-content" always;'
    )
    lines.append(
        '    add_header Strict-Transport-Security '
        '"max-age=31536000; includeSubDomains; preload" always;'
    )
    lines.append("")
    lines.append(
        textwrap.indent(
            proxy_block(
                port,
                trailing_slash=trailing_slash,
                external_websockets=external_websockets,
                proxy_target=proxy_target,
            ),
            "    ",
        )
    )
    lines.append("}")
    return _format_server_block(lines)


def write_if_changed(path: Path, content: str, *, sudo: str | None = None) -> bool:
    """Write *content* to *path* if it differs from the existing file."""

    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8")
        except OSError:
            existing = None
        if existing == content:
            return False

    if sudo:
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as temp_file:
            temp_file.write(content)
            temp_path = Path(temp_file.name)

        try:
            subprocess.run([sudo, "mkdir", "-p", str(path.parent)], check=True)
            subprocess.run([sudo, "cp", str(temp_path), str(path)], check=True)
        finally:
            temp_path.unlink(missing_ok=True)
        return True

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True
