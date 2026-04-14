from __future__ import annotations

import json
import signal
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.netmesh.models import MeshMembership, NetmeshAgentStatus
from apps.netmesh.services.agent import NetmeshAgentConfig, NetmeshAgentRuntime
from apps.netmesh.services.agent_lifecycle import NetmeshLifecycle
from apps.nodes.models import Node


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
    assert status.is_running is False


@pytest.mark.django_db
def test_netmesh_lifecycle_signal_handler_only_marks_shutdown():
    lifecycle = NetmeshLifecycle()
    lifecycle._request_shutdown(signal.SIGTERM, None)

    assert lifecycle.shutdown_requested is True
    assert lifecycle.shutdown_signal == signal.SIGTERM


@pytest.mark.django_db
def test_netmesh_lifecycle_stopped_preserves_last_error_when_not_overridden():
    lifecycle = NetmeshLifecycle()
    status = NetmeshAgentStatus.get_solo()
    status.last_error = "prior error"
    status.save(update_fields=["last_error"])

    lifecycle.mark_stopped(state="stopped")
    status.refresh_from_db()

    assert status.is_running is False
    assert status.last_error == "prior error"


@pytest.mark.django_db
def test_netmesh_lifecycle_restores_signal_handlers():
    previous_sigterm_handler = signal.getsignal(signal.SIGTERM)
    previous_sigint_handler = signal.getsignal(signal.SIGINT)

    with NetmeshLifecycle():
        assert signal.getsignal(signal.SIGTERM) != previous_sigterm_handler
        assert signal.getsignal(signal.SIGINT) != previous_sigint_handler

    assert signal.getsignal(signal.SIGTERM) == previous_sigterm_handler
    assert signal.getsignal(signal.SIGINT) == previous_sigint_handler


@pytest.mark.django_db
def test_netmesh_agent_runtime_sleep_is_shutdown_responsive(monkeypatch):
    runtime = NetmeshAgentRuntime(
        config=NetmeshAgentConfig(
            api_base_url="https://example.invalid",
            enrollment_token="nmt1_test_token",
            poll_interval_seconds=30.0,
            max_loops=None,
        )
    )

    monkeypatch.setattr(
        NetmeshAgentRuntime,
        "_sync_cycle",
        lambda _self: {"peers_synced": 0},
    )

    sleep_steps: list[float] = []

    def fake_sleep(value: float):
        sleep_steps.append(value)
        runtime.lifecycle.shutdown_requested = True

    monkeypatch.setattr("apps.netmesh.services.agent.sleep", fake_sleep)

    loops_completed = runtime.run()

    assert loops_completed == 1
    assert sleep_steps == [1.0]
