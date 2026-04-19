from __future__ import annotations

import json
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.netmesh.models import NodeKeyMaterial, PeerPolicy
from apps.nodes.models import Node, NodeEnrollmentEvent


@pytest.mark.django_db
def test_netmesh_enroll_token_emits_json_payload():
    node = Node.objects.create(hostname="mesh-enroll")
    stream = StringIO()

    call_command("netmesh", "enroll-token", str(node.id), stdout=stream)

    payload = json.loads(stream.getvalue())
    assert payload["node_id"] == node.id
    assert payload["token"].startswith("nmt1_")
    assert payload["scope"] == "mesh:read"


@pytest.mark.django_db
def test_netmesh_policy_check_raises_for_invalid_rule():
    source = Node.objects.create(hostname="mesh-policy-src")
    destination = Node.objects.create(hostname="mesh-policy-dst")
    policy = PeerPolicy.objects.create(
        tenant="mesh-policy",
        source_node=source,
        destination_node=destination,
        allowed_services=["ocpp"],
        denied_services=["ocpp"],
    )
    stream = StringIO()

    with pytest.raises(CommandError):
        call_command("netmesh", "policy", "check", stdout=stream)

    payload = json.loads(stream.getvalue())
    assert payload["errors"][0]["policy_id"] == policy.id


@pytest.mark.django_db
def test_netmesh_schedule_rotation_creates_events():
    node = Node.objects.create(hostname="mesh-rotation")
    key = NodeKeyMaterial.objects.create(node=node, public_key="abc", revoked=False)
    key.created_at = timezone.now() - timedelta(days=90)
    key.save(update_fields=["created_at"])
    stream = StringIO()

    call_command("netmesh", "schedule-rotation", "--max-age-days", "30", stdout=stream)

    payload = json.loads(stream.getvalue())
    assert payload["scheduled"] == 1
    assert NodeEnrollmentEvent.objects.filter(
        node=node,
        action=NodeEnrollmentEvent.Action.KEY_ROTATED,
    ).exists()
