from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .models import Node


def _resolve_host_name(node: Node) -> str:
    """Return a stable inventory host name for ``node``."""

    for candidate in (
        (node.public_endpoint or "").strip(),
        (node.network_hostname or "").strip(),
        (node.hostname or "").strip(),
    ):
        if candidate:
            return candidate
    if node.pk:
        return f"node-{node.pk}"
    return f"node-{node.uuid}"


def render_inventory_host(node: Node) -> dict[str, Any]:
    """Return a host definition suitable for an Ansible inventory."""

    host_name = _resolve_host_name(node)
    role_name = ""
    if node.role_id:
        role = getattr(node, "role", None)
        if role is not None and getattr(role, "name", None):
            role_name = role.name
        else:
            from .models import NodeRole  # Local import to avoid circulars during startup

            role_name = (
                NodeRole.objects.filter(pk=node.role_id)
                .values_list("name", flat=True)
                .first()
                or ""
            )

    features = list(
        node.features.order_by("slug").values_list("slug", flat=True)
        if node.pk
        else []
    )

    ipv4_addresses = node.get_ipv4_addresses() if node.pk else []
    best_ip = node.get_best_ip() if node.pk else ""
    primary_contact = node.get_primary_contact() if node.pk else ""

    ansible_host = best_ip or (primary_contact or host_name)

    host_vars: dict[str, Any] = {
        "ansible_host": ansible_host,
        "ansible_port": node.port,
        "node_id": node.pk,
        "node_uuid": str(node.uuid),
        "node_role": role_name,
        "node_hostname": node.hostname,
        "node_network_hostname": node.network_hostname,
        "node_public_endpoint": node.public_endpoint,
        "node_constellation_ip": node.constellation_ip,
        "node_ipv6_address": node.ipv6_address,
        "node_ipv4_addresses": ipv4_addresses,
        "node_mac_address": node.mac_address,
        "node_features": features,
        "node_is_local": node.is_local,
    }

    profile = node.get_node_profile()
    profile_data = node.get_profile_data()
    if profile is not None:
        host_vars["node_profile_name"] = profile.name
    if profile_data:
        host_vars["node_profile"] = profile_data
        for key, value in profile_data.items():
            host_vars.setdefault(key, value)

    return {"name": host_name, "vars": host_vars}


def build_inventory(node: Node, inventory_group: str | None = None) -> dict[str, Any]:
    """Return an Ansible inventory structure for ``node``.

    Parameters
    ----------
    node:
        Node instance to expose to Ansible.
    inventory_group:
        Optional group name from the role configuration profile. When provided,
        the host will be nested under that group while remaining reachable
        through ``all.hosts`` for convenience.
    """

    host_definition = render_inventory_host(node)
    host_name = host_definition["name"]
    host_vars = host_definition["vars"]

    inventory: dict[str, Any] = {
        "all": {
            "hosts": {host_name: host_vars},
        }
    }

    if inventory_group:
        children = inventory["all"].setdefault("children", {})
        children[inventory_group] = {"hosts": {host_name: host_vars}}

    return inventory
