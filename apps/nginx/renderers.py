from __future__ import annotations

import json
from pathlib import Path

from apps.nginx.config_utils import (
    default_reject_server,
    http_proxy_server,
    http_redirect_server,
    https_proxy_server,
    slugify,
    websocket_map,
)

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

HTTPS_IPV4_LISTENS = (
    "443 ssl",
    "8443 ssl",
)
HTTPS_IPV6_LISTENS = (
    "[::]:443 ssl",
    "[::]:8443 ssl",
)
PRIMARY_PUBLIC_SERVER_NAMES = "arthexis.com *.arthexis.com"


def _build_server_names(domain: str, prefixes: list[str]) -> str:
    """Return unique server names for a managed site domain and its prefixes.

    Parameters:
        domain: The base domain configured for the managed site.
        prefixes: Optional subdomain prefixes to expand onto the base domain.

    Returns:
        A space-separated server_name string with duplicates removed.
    """

    names = [domain]
    for prefix in prefixes:
        names.append(f"{prefix}.{domain}")
    return " ".join(dict.fromkeys(names))


def _https_listens(include_ipv6: bool) -> list[str]:
    """Build the HTTPS listen directives for generated nginx server blocks.

    Parameters:
        include_ipv6: Whether IPv6 HTTPS listeners should be included.

    Returns:
        The ordered HTTPS listen directives for the generated config.
    """

    https_listens = list(HTTPS_IPV4_LISTENS)
    if include_ipv6:
        https_listens.extend(HTTPS_IPV6_LISTENS)
    return https_listens


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
) -> str:
    """Render the primary nginx config for public or internal deployments.

    Parameters:
        mode: Deployment mode, either ``internal`` or ``public``.
        port: Upstream application port.
        certificate: Optional certificate object with path attributes.
        http_server_names: Optional HTTP server_name override.
        https_server_names: Optional HTTPS server_name override.
        https_enabled: Whether HTTPS blocks should be rendered.
        include_ipv6: Whether IPv6 listeners should be added.
        external_websockets: Whether websocket support directives are enabled.

    Returns:
        The rendered nginx configuration for the primary site.

    Raises:
        ValueError: If *mode* is not supported.
    """

    mode = mode.lower()
    if mode not in {"internal", "public"}:
        raise ValueError(f"Unsupported mode: {mode}")

    http_listens = list(HTTP_IPV4_LISTENS)
    if include_ipv6:
        http_listens.extend(HTTP_IPV6_LISTENS)

    https_listens: list[str] = []
    if https_enabled:
        https_listens = _https_listens(include_ipv6)

    certificate_path = getattr(certificate, "certificate_path", None)
    certificate_key_path = getattr(certificate, "certificate_key_path", None)

    prefix_blocks: list[str] = []
    proxy_target = f"127.0.0.1:{port}"

    if external_websockets:
        prefix_blocks.insert(0, websocket_map())

    if mode == "public":
        http_names = http_server_names or PRIMARY_PUBLIC_SERVER_NAMES
        https_names = https_server_names or PRIMARY_PUBLIC_SERVER_NAMES
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
    include_ipv6: bool = False,
    external_websockets: bool = True,
    proxy_target: str | None = None,
    subdomain_prefixes: list[str] | None = None,
    excluded_domains: set[str] | None = None,
) -> str:
    """Render managed site server blocks from staged site definitions.

    Parameters:
        config_path: Path to the staged managed-site JSON definitions.
        mode: Deployment mode, either ``internal`` or ``public``.
        port: Upstream application port.
        https_enabled: Whether HTTPS blocks should be rendered.
        include_ipv6: Whether IPv6 HTTPS listeners should be included.
        external_websockets: Whether websocket support directives are enabled.
        proxy_target: Optional upstream hostname override.
        subdomain_prefixes: Optional managed-site subdomain prefixes.
        excluded_domains: Optional set of domains to omit from managed-site rendering
            (case-insensitive).

    Returns:
        The rendered nginx server blocks for managed sites.

    Raises:
        ValueError: If the site definition file contains invalid JSON.
    """

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
    https_listens = _https_listens(include_ipv6) if https_enabled else []
    excluded = {domain.strip().lower() for domain in (excluded_domains or set()) if domain.strip()}

    site_blocks: list[str] = ["# Autogenerated by apps.nginx.renderers"]

    for entry in sites:
        domain = (entry.get("domain") or "").strip()
        if not domain:
            continue
        if domain.lower() in excluded:
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
                    listens=https_listens,
                    external_websockets=external_websockets,
                    proxy_target=proxy_target,
                )
            )
        elif require_https:
            blocks.append("# HTTPS requested but unavailable in this configuration.")

        site_blocks.append("\n\n".join(blocks))

    if len(site_blocks) == 1:
        site_blocks.append("# No managed sites configured.")

    content = "\n\n".join(site_blocks)
    return content


def generate_unified_config(
    mode: str,
    port: int,
    *,
    certificate=None,
    https_enabled: bool = False,
    include_ipv6: bool = False,
    external_websockets: bool = True,
    site_config_path: Path | None = None,
    subdomain_prefixes: list[str] | None = None,
) -> str:
    """Return the single nginx config that combines primary and managed sites.

    Parameters:
        mode: Deployment mode, either ``internal`` or ``public``.
        port: Upstream application port.
        certificate: Optional certificate object with path attributes.
        https_enabled: Whether HTTPS blocks should be rendered.
        include_ipv6: Whether IPv6 listeners should be added.
        external_websockets: Whether websocket support directives are enabled.
        site_config_path: Optional path to staged managed-site definitions.
        subdomain_prefixes: Optional managed-site subdomain prefixes.

    Returns:
        The rendered unified nginx configuration.
    """

    primary_content = generate_primary_config(
        mode,
        port,
        certificate=certificate,
        https_enabled=https_enabled,
        include_ipv6=include_ipv6,
        external_websockets=external_websockets,
    ).rstrip()

    parts = [primary_content]

    if site_config_path is not None:
        excluded_domains: set[str] = set()
        if mode.lower() == "public":
            excluded_domains.update(
                domain.lower() for domain in PRIMARY_PUBLIC_SERVER_NAMES.split() if "*" not in domain
            )
        managed_content = generate_site_entries_content(
            site_config_path,
            mode,
            port,
            https_enabled=https_enabled,
            include_ipv6=include_ipv6,
            external_websockets=external_websockets,
            subdomain_prefixes=subdomain_prefixes,
            excluded_domains=excluded_domains,
        ).rstrip()
        parts.append(managed_content)

    return "\n\n".join(parts) + "\n"
