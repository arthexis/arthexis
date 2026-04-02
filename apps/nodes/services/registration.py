"""Node registration services."""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import socket
from typing import TYPE_CHECKING

from django.conf import settings
from django.utils.text import slugify

from apps.nodes.logging import get_register_local_node_logger
from apps.nodes.models.role import NodeRole
from apps.nodes.models.utils import ROLE_RENAMES
from utils import revision

if TYPE_CHECKING:
    from apps.nodes.models.node import Node

local_registration_logger = get_register_local_node_logger()


def _resolve_local_role_name() -> str:
    """Resolve the local node role from settings, environment, lock file, then default."""
    env_role = str(os.environ.get("NODE_ROLE", "")).strip()
    if env_role:
        configured_role = env_role.title()
        return ROLE_RENAMES.get(configured_role, configured_role)

    configured_role = getattr(settings, "NODE_ROLE", "")
    configured_role = "" if configured_role is None else str(configured_role).strip()
    if configured_role and configured_role.lower() != "terminal":
        normalized_setting_role = configured_role.title()
        return ROLE_RENAMES.get(normalized_setting_role, normalized_setting_role)

    role_lock = Path(settings.BASE_DIR) / ".locks" / "role.lck"
    try:
        locked_role = role_lock.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError, UnicodeError):
        locked_role = ""
    if locked_role:
        normalized_lock_role = locked_role.title()
        return ROLE_RENAMES.get(normalized_lock_role, normalized_lock_role)

    if configured_role:
        normalized_setting_role = configured_role.title()
        return ROLE_RENAMES.get(normalized_setting_role, normalized_setting_role)

    return "Terminal"


def register_current(node_model: type["Node"], notify_peers: bool = True) -> tuple["Node", bool]:
    """Create or update the local node entry for ``node_model``."""
    hostname_override = (os.environ.get("NODE_HOSTNAME") or os.environ.get("HOSTNAME") or "").strip()
    hostname = hostname_override or socket.gethostname()
    network_hostname = os.environ.get("NODE_PUBLIC_HOSTNAME", "").strip()
    if not network_hostname:
        fqdn = socket.getfqdn(hostname)
        if fqdn and "." in fqdn:
            network_hostname = fqdn

    ipv4_override = os.environ.get("NODE_PUBLIC_IPV4", "").strip()
    ipv6_override = os.environ.get("NODE_PUBLIC_IPV6", "").strip()
    ipv4_candidates: list[str] = []
    ipv6_candidates: list[str] = []
    for override, version in ((ipv4_override, 4), (ipv6_override, 6)):
        if not override:
            continue
        try:
            parsed = ipaddress.ip_address(override)
        except ValueError:
            continue
        if parsed.version == version:
            (ipv4_candidates if version == 4 else ipv6_candidates).append(override)

    resolve_hosts: list[str] = []
    for value in (network_hostname, hostname_override, hostname):
        value = (value or "").strip()
        if value and value not in resolve_hosts:
            resolve_hosts.append(value)

    resolved_ipv4, resolved_ipv6 = node_model._resolve_ip_addresses(*resolve_hosts)
    ipv4_candidates.extend(ip for ip in resolved_ipv4 if ip not in ipv4_candidates)
    ipv6_candidates.extend(ip for ip in resolved_ipv6 if ip not in ipv6_candidates)

    try:
        direct_address = socket.gethostbyname(hostname)
    except OSError:
        direct_address = ""
    if direct_address and direct_address not in ipv4_candidates:
        ipv4_candidates.append(direct_address)

    ordered_ipv4 = node_model.order_ipv4_addresses(node_model.sanitize_ipv4_addresses(ipv4_candidates))
    ipv4_address = ordered_ipv4[0] if ordered_ipv4 else ""
    serialized_ipv4 = ",".join(ordered_ipv4) if ordered_ipv4 else ""
    ipv6_address = node_model._select_preferred_ip(ipv6_candidates) or ""

    managed_site, site_domain, site_requires_https = node_model._detect_managed_site()
    preferred_contact = ipv4_address or ipv6_address or direct_address or "127.0.0.1"
    if site_domain:
        hostname = site_domain
        network_hostname = site_domain
        preferred_contact = site_domain

    port = node_model.get_preferred_port()
    if site_domain:
        port = node_model._preferred_site_port(site_requires_https)
    base_path = str(node_model.default_base_path())
    ver_path = Path(settings.BASE_DIR) / "VERSION"
    installed_version = ver_path.read_text().strip() if ver_path.exists() else ""
    rev_value = revision.get_revision()
    installed_revision = rev_value if rev_value else ""
    mac = node_model.get_current_mac()
    host_instance_id = node_model.get_host_instance_id()
    local_registration_logger.info("Local node registration started hostname=%s mac=%s", hostname, mac)

    endpoint_override = os.environ.get("NODE_PUBLIC_ENDPOINT", "").strip()
    slug = slugify(endpoint_override or hostname) or node_model._generate_unique_public_endpoint(hostname or mac)
    node = node_model.objects.filter(mac_address=mac).first() or node_model.objects.filter(public_endpoint=slug).first()

    defaults = {
        "hostname": hostname,
        "network_hostname": network_hostname,
        "ipv4_address": serialized_ipv4,
        "ipv6_address": ipv6_address,
        "address": preferred_contact,
        "port": port,
        "trusted": True,
        "base_path": base_path,
        "installed_version": installed_version,
        "installed_revision": installed_revision,
        "public_endpoint": slug,
        "mac_address": mac,
        "host_instance_id": host_instance_id,
        "current_relation": node_model.Relation.SELF,
    }
    if managed_site:
        defaults["base_site"] = managed_site

    role_name = _resolve_local_role_name()
    desired_role = NodeRole.objects.filter(name=role_name).first()

    if node:
        update_fields: list[str] = []
        for field, value in defaults.items():
            current = getattr(node, field)
            if isinstance(value, str):
                value = value or ""
                current = current or ""
            if current != value:
                setattr(node, field, value)
                update_fields.append(field)
        if desired_role and node.role_id != desired_role.id:
            node.role = desired_role
            update_fields.append("role")
        if update_fields:
            node.save(update_fields=update_fields)
            local_registration_logger.info("Local node registration updated node_id=%s endpoint=%s address=%s", node.id, node.public_endpoint, node.address)
        else:
            node.refresh_features()
            local_registration_logger.info("Local node registration refreshed node_id=%s endpoint=%s address=%s", node.id, node.public_endpoint, node.address)
        created = False
    else:
        node = node_model.objects.create(**defaults)
        created = True
        if desired_role:
            node.role = desired_role
            node.save(update_fields=["role"])
        local_registration_logger.info("Local node registration created node_id=%s endpoint=%s address=%s", node.id, node.public_endpoint, node.address)

    if created and node.role is None:
        terminal = NodeRole.objects.filter(name="Terminal").first()
        if terminal:
            node.role = terminal
            node.save(update_fields=["role"])

    node.ensure_keys()
    if notify_peers:
        node.notify_peers_of_update()
    return node, created
