from __future__ import annotations

from django.urls import reverse


def site_badge_data(*, site=None, **_kwargs) -> dict[str, object]:
    """Return admin badge payload for the current site."""

    return {
        "value": (site.name or site.domain) if site else "Unknown",
        "url": reverse("admin:pages_siteproxy_change", args=[site.pk]) if site else None,
        "present": bool(site),
    }


def node_badge_data(*, node=None, **_kwargs) -> dict[str, object]:
    """Return admin badge payload for the current node."""

    return {
        "value": node.hostname if node else "Unknown",
        "url": reverse("admin:nodes_node_change", args=[node.pk]) if node else None,
        "present": bool(node),
    }


def role_badge_data(*, role=None, **_kwargs) -> dict[str, object]:
    """Return admin badge payload for the current node role."""

    return {
        "value": role.name if role else "Unknown",
        "url": reverse("admin:nodes_noderole_change", args=[role.pk]) if role else None,
        "present": bool(role),
    }
