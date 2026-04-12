from __future__ import annotations

import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.netmesh.models import MeshMembership, NetmeshAgentStatus
from apps.nodes.models import Node
from apps.netmesh.services.agent import NetmeshAgentRuntime


@pytest.mark.django_db
def test_netmesh_agent_command_requires_enrollment_token():
    with pytest.raises(CommandError):
        call_command("netmesh_agent", "--max-loops", "1")


@pytest.mark.django_db
def test_netmesh_agent_sync_updates_status_and_emits_summary(monkeypatch):
    source = Node.objects.create(hostname="agent-source", mesh_enrollment_state=Node.MeshEnrollmentState.ENROLLED)
    peer = Node.objects.create(hostname="agent-peer", mesh_enrollment_state=Node.MeshEnrollmentState.ENROLLED)
    MeshMembership.objects.create(node=source, tenant="arthexis", site=None, is_enabled=True)
    MeshMembership.objects.create(node=peer, tenant="arthexis", site=None, is_enabled=True)
    responses = {
        "peers/": {"peers": [{"node_id": peer.id, "hostname": peer.hostname}]},
        "peer-endpoints/": {"endpoints": []},
    }

    def fake_request_json(self, path: str):
        return responses[path]

    monkeypatch.setattr(NetmeshAgentRuntime, "_request_json", fake_request_json)

    stream = StringIO()
    call_command(
        "netmesh_agent",
        "--enrollment-token",
        "nmt1_test_token",
        "--poll-interval",
        "0.1",
        "--max-loops",
        "1",
        stdout=stream,
    )

    payload = json.loads(stream.getvalue())
    assert payload["status"] == "stopped"
    assert payload["loops_completed"] == 1
    assert payload["peers_synced"] == 1

    status = NetmeshAgentStatus.get_solo()
    assert status.last_poll_at is not None
    assert status.peers_synced == 1
    assert status.session_count == 0
    assert status.is_running is False
