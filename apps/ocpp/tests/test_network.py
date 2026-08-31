"""Tests for OCPP network metadata synchronization."""

import pytest

from apps.nodes.models import Node
from apps.ocpp.models import Charger
from apps.ocpp.network import apply_remote_charger_payload

pytestmark = pytest.mark.django_db


def test_apply_remote_charger_payload_preserves_local_manager_node():
    """Remote charger metadata must not clear receiver-owned manager routing."""

    source = Node.objects.create(
        hostname="metadata-source",
        mac_address="00:11:22:33:44:01",
    )
    manager = Node.objects.create(
        hostname="metadata-manager",
        mac_address="00:11:22:33:44:02",
    )
    charger = Charger.objects.create(
        charger_id="CP-METADATA-PRESERVE",
        export_transactions=True,
        manager_node=manager,
    )

    updated = apply_remote_charger_payload(
        source,
        {
            "charger_id": charger.charger_id,
            "connector_id": None,
            "display_name": "Remote display",
        },
    )

    assert updated is not None
    assert updated.manager_node_id == manager.pk
    assert updated.node_origin_id == source.pk
