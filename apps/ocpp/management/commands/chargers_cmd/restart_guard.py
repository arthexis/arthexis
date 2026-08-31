from __future__ import annotations

from django.core.management.base import CommandError

from apps.nodes.models import Node
from apps.ocpp.models import Charger


def require_local_restart_targets(chargers: list[Charger]) -> None:
    """Reject restart targets that originate from a downstream node."""

    local = None
    for charger in chargers:
        if charger.node_origin_id is None:
            continue
        if local is None:
            local = Node.get_local()
        if local is not None and charger.node_origin_id == local.pk:
            continue
        origin = str(charger.node_origin) if charger.node_origin else "its origin node"
        raise CommandError(
            "Refusing to restart downstream charger "
            f"{charger.charger_id}; run this on {origin} or use an explicit "
            "downstream operation."
        )
