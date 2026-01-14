from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from apps.nginx.config_utils import (
    default_reject_server,
    http_proxy_server,
    http_redirect_server,
    https_proxy_server,
    slugify,
    websocket_map,
    write_if_changed,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle guard for type checkers
    from apps.nginx.services import SecondaryInstance

HTTP_IPV4_LISTENS = (
    "0.0.0.0:80",
    "0.0.0.0:8000",
    "0.0.0.0:8080",
    "0.0.0.0:8900",
)

HTTP_IPV6_LISTENS = (
    "[::]:80",
    "[::]:8000",
    "[::]:8080",
    "[::]:8900",
)

HTTPS_IPV4_LISTENS = ("443 ssl",)
HTTPS_IPV6_LISTENS = ("[::]:443 ssl",)


def _build_server_names(domain: str, prefixes: list[str]) -> str:
    names = [domain]
    for prefix in prefixes:
        names.append(f"{prefix}.{domain}")
    return " ".join(dict.fromkeys(names))


def upstream_block(upstream_name: str, primary_port: int, backup_port: int | None = None) -> str:
    lines = [
        f"upstream {upstream_name} {{",
        f"    server 127.0.0.1:{primary_port};",
    ]
    if backup_port:
        lines.append(f"    server 127.0.0.1:{backup_port} backup;")
    lines.append("}")
    return "\n".join(lines)


def generate_primary_config(
    mode: str,
    port: int,
    *,
    certificate=None,
    http_server_names: str | None = None,
    https_server_names: str | None = None,
    https_enabled: bool = False,
    include_ipv6: bool = False,
    external_websockets: bool = True,
    secondary_instance: "SecondaryInstance | None" = None,
) -> str:
    mode = mode.lower()
    if mode not in {"internal", "public"}:
        raise ValueError(f"Unsupported mode: {mode}")

    http_listens = list(HTTP_IPV4_LISTENS)
    if include_ipv6:
        http_listens.extend(HTTP_IPV6_LISTENS)

    https_listens: list[str] = []
    if https_enabled:
        https_listens = list(HTTPS_IPV4_LISTENS)
        if include_ipv6:
            https_listens.extend(HTTPS_IPV6_LISTENS)

    certificate_path = getattr(certificate, "certificate_path", None)
    certificate_key_path = getattr(certificate, "certificate_key_path", None)

    proxy_target = f"127.0.0.1:{port}"
    prefix_blocks: list[str] = []
    if secondary_instance:
        upstream_name = f"arthexis-{slugify(secondary_instance.name)}-pool"
        prefix_blocks.append(
            upstream_block(upstream_name, port, getattr(secondary_instance, "port", None))
        )
        proxy_target = upstream_name

    if external_websockets:
        prefix_blocks.insert(0, websocket_map())

    if mode == "public":
        http_names = http_server_names or "arthexis.com *.arthexis.com"
        https_names = https_server_names or "arthexis.com *.arthexis.com"
        if https_enabled:
            http_block = http_redirect_server(http_names, listens=http_listens)
        else:
            http_block = http_proxy_server(
                http_names,
                port,
                http_listens,
                trailing_slash=False,
                external_websockets=external_websockets,
                proxy_target=proxy_target,
            )
        http_default = default_reject_server(http_listens)

        blocks = [*prefix_blocks, http_block, http_default]
        if https_enabled:
            https_block = https_proxy_server(
                https_names,
                port,
                listens=https_listens,
                certificate_path=certificate_path,
                certificate_key_path=certificate_key_path,
                trailing_slash=False,
                external_websockets=external_websockets,
                proxy_target=proxy_target,
            )
            https_default = default_reject_server(
                https_listens,
                https=True,
                certificate_path=certificate_path,
                certificate_key_path=certificate_key_path,
            )
            blocks.extend([https_block, https_default])
        return "\n\n".join(blocks) + "\n"

    http_names = http_server_names or "_"
    http_block = http_proxy_server(
        http_names,
        port,
        http_listens,
        trailing_slash=False,
        external_websockets=external_websockets,
        proxy_target=proxy_target,
    )
    blocks = [*prefix_blocks, http_block]

    if https_enabled:
        https_block = https_proxy_server(
            https_server_names or http_names,
            port,
            listens=https_listens,
            certificate_path=certificate_path,
            certificate_key_path=certificate_key_path,
            trailing_slash=False,
            external_websockets=external_websockets,
            proxy_target=proxy_target,
        )
        blocks.append(https_block)
    return "\n\n".join(blocks) + "\n"


def generate_site_entries_content(
    config_path: Path,
    mode: str,
    port: int,
    *,
    https_enabled: bool = False,
    external_websockets: bool = True,
    proxy_target: str | None = None,
    subdomain_prefixes: list[str] | None = None,
) -> str:
    try:
        raw = config_path.read_text(encoding="utf-8")
        sites = json.loads(raw)
    except FileNotFoundError:
        sites = []
    except json.JSONDecodeError as exc:  # pragma: no cover - invalid staging file
        raise ValueError(f"Invalid JSON in {config_path}: {exc}")

    seen_domains: set[str] = set()
    mode = mode.lower()
    prefixes = [prefix for prefix in (subdomain_prefixes or []) if prefix]

    site_blocks: list[str] = ["# Autogenerated by apps.nginx.renderers"]

    for entry in sites:
        domain = (entry.get("domain") or "").strip()
        if not domain:
            continue
        require_https = bool(entry.get("require_https"))
        slug = slugify(domain)
        if slug in seen_domains:
            continue
        seen_domains.add(slug)

        server_names = _build_server_names(domain, prefixes)
        blocks: list[str] = [f"# Managed site for {domain}"]

        if require_https and mode == "public" and https_enabled:
            blocks.append(http_redirect_server(server_names))
        else:
            blocks.append(
                http_proxy_server(
                    server_names,
                    port,
                    external_websockets=external_websockets,
                    proxy_target=proxy_target,
                )
            )

        if mode == "public" and https_enabled:
            blocks.append(
                https_proxy_server(
                    server_names,
                    port,
                    external_websockets=external_websockets,
                    proxy_target=proxy_target,
                )
            )
        elif require_https:
            blocks.append(
                "# HTTPS requested but unavailable in this configuration."
            )

        site_blocks.append("\n\n".join(blocks))

    if len(site_blocks) == 1:
        site_blocks.append("# No managed sites configured.")

    content = "\n\n".join(site_blocks)
    return content


def apply_site_entries(
    config_path: Path,
    mode: str,
    port: int,
    dest_path: Path,
    *,
    https_enabled: bool = False,
    external_websockets: bool = True,
    proxy_target: str | None = None,
    subdomain_prefixes: list[str] | None = None,
    sudo: str | None = None,
) -> bool:
    content = generate_site_entries_content(
        config_path,
        mode,
        port,
        https_enabled=https_enabled,
        external_websockets=external_websockets,
        proxy_target=proxy_target,
        subdomain_prefixes=subdomain_prefixes,
    )
    return write_if_changed(dest_path, content, sudo=sudo)
