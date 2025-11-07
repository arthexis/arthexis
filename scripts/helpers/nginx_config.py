#!/usr/bin/env python3
"""Reusable helpers for generating nginx configuration blocks."""

from __future__ import annotations

import re
import textwrap
from pathlib import Path
from typing import Iterable

DEFAULT_CERT_DIR = Path("/etc/letsencrypt/live/arthexis.com")
CERTIFICATE_PATH = DEFAULT_CERT_DIR / "fullchain.pem"
CERTIFICATE_KEY_PATH = DEFAULT_CERT_DIR / "privkey.pem"
SSL_OPTIONS_PATH = Path("/etc/letsencrypt/options-ssl-nginx.conf")
SSL_DHPARAM_PATH = Path("/etc/letsencrypt/ssl-dhparams.pem")
MAINTENANCE_ROOT = Path("/usr/share/arthexis-fallback")


def slugify(domain: str) -> str:
    """Return a filesystem-friendly slug for *domain*."""
    slug = re.sub(r"[^a-z0-9]+", "-", domain.lower()).strip("-")
    return slug or "site"


def maintenance_block() -> str:
    """Return the shared maintenance configuration block."""
    return textwrap.dedent(
        f"""
        error_page 500 502 503 504 /maintenance/index.html;

        location = /maintenance/index.html {{
            root {MAINTENANCE_ROOT};
            add_header Cache-Control \"no-store\";
        }}

        location /maintenance/ {{
            alias {MAINTENANCE_ROOT}/;
            add_header Cache-Control \"no-store\";
        }}
        """
    ).strip()


def proxy_block(port: int, *, trailing_slash: bool = True) -> str:
    """Return the proxy pass configuration block for *port*."""
    upstream = f"http://127.0.0.1:{port}"
    if trailing_slash:
        upstream += "/"

    return textwrap.dedent(
        f"""
        location / {{
            proxy_pass {upstream};
            proxy_intercept_errors on;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection \"upgrade\";
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }}
        """
    ).strip()


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


def http_proxy_server(
    server_names: str,
    port: int,
    listens: Iterable[str] | None = None,
    *,
    trailing_slash: bool = True,
) -> str:
    """Return an HTTP proxy server block for *server_names*."""
    if listens is None:
        listens = ("80",)

    lines: list[str] = ["server {"]
    for listen in _unique_preserve_order(listens):
        lines.append(f"    listen {listen};")
    lines.append(f"    server_name {server_names};")
    lines.append("")
    lines.append(textwrap.indent(maintenance_block(), "    "))
    lines.append("")
    lines.append(textwrap.indent(proxy_block(port, trailing_slash=trailing_slash), "    "))
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


def https_proxy_server(
    server_names: str,
    port: int,
    listens: Iterable[str] | None = None,
    *,
    trailing_slash: bool = True,
) -> str:
    """Return an HTTPS proxy server block for *server_names*."""
    if listens is None:
        listens = ("443 ssl",)

    lines: list[str] = ["server {"]
    for listen in _unique_preserve_order(listens):
        lines.append(f"    listen {listen};")
    lines.append(f"    server_name {server_names};")
    lines.append("")
    lines.append(textwrap.indent(maintenance_block(), "    "))
    lines.append("")
    lines.extend(
        [
            f"    ssl_certificate {CERTIFICATE_PATH};",
            f"    ssl_certificate_key {CERTIFICATE_KEY_PATH};",
            f"    include {SSL_OPTIONS_PATH};",
            f"    ssl_dhparam {SSL_DHPARAM_PATH};",
            "",
        ]
    )
    lines.append(textwrap.indent(proxy_block(port, trailing_slash=trailing_slash), "    "))
    lines.append("}")
    return _format_server_block(lines)


def write_if_changed(path: Path, content: str) -> bool:
    """Write *content* to *path* when it differs, returning ``True`` if updated."""
    try:
        existing = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing = None

    normalized = content.rstrip("\n")
    if existing is not None and existing.rstrip("\n") == normalized:
        return False

    path.write_text(normalized + "\n", encoding="utf-8")
    return True
