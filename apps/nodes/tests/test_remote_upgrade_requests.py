from __future__ import annotations

import json
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.nodes.models import (
    NetMessage,
    Node,
    NodeRole,
    PendingNetMessage,
    RemoteUpgradeRequest,
)
from apps.nodes.services import propagation, remote_upgrade


@pytest.fixture
def local_node(monkeypatch):
    node = Node.objects.create(
        hostname="local-node",
        mac_address="aa:bb:cc:dd:ee:01",
        address="198.51.100.1",
        current_relation=Node.Relation.SELF,
    )
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: node))
    return node


@pytest.fixture
def downstream_node():
    return Node.objects.create(
        hostname="downstream-node",
        mac_address="aa:bb:cc:dd:ee:02",
        address="198.51.100.2",
        current_relation=Node.Relation.DOWNSTREAM,
    )


@pytest.fixture
def upstream_node():
    return Node.objects.create(
        hostname="upstream-node",
        mac_address="aa:bb:cc:dd:ee:03",
        address="198.51.100.3",
        current_relation=Node.Relation.UPSTREAM,
    )


@pytest.mark.django_db
def test_create_remote_upgrade_request_queues_targeted_net_message(
    monkeypatch, local_node, downstream_node
):
    propagated: list[NetMessage] = []
    monkeypatch.setattr(
        NetMessage,
        "propagate",
        lambda self, seen=None, **kwargs: propagated.append(self),
    )

    request = remote_upgrade.create_remote_upgrade_request(
        target=downstream_node,
        channel="latest",
        reason="maintenance",
    )

    message = NetMessage.objects.get(kind=NetMessage.Kind.REMOTE_UPGRADE_REQUEST)
    assert request.status == RemoteUpgradeRequest.Status.REQUESTED
    assert request.origin_node == local_node
    assert request.target_node == downstream_node
    assert request.channel == "unstable"
    assert message.filter_node == downstream_node
    assert message.target_limit == 1
    assert message.control_payload["remote_upgrade_request"]["uuid"] == str(request.uuid)
    assert propagated == [message]


@pytest.mark.django_db
def test_receive_remote_upgrade_request_rejects_when_disabled(
    monkeypatch, settings, local_node, upstream_node
):
    responses: list[tuple[str, int]] = []
    settings.NODE_ROLE = "Terminal"
    monkeypatch.delenv(remote_upgrade.REMOTE_UPGRADE_ENV, raising=False)
    monkeypatch.setattr(
        remote_upgrade,
        "is_suite_feature_enabled",
        lambda slug, default=True: slug == remote_upgrade.REMOTE_UPGRADE_FEATURE_SLUG,
    )
    monkeypatch.setattr(
        remote_upgrade,
        "_send_remote_upgrade_response",
        lambda request, target: responses.append((request.status, target.pk)),
    )

    request_uuid = "11111111-1111-4111-8111-111111111111"
    request = remote_upgrade.receive_remote_upgrade_request(
        {
            "remote_upgrade_request": {
                "uuid": request_uuid,
                "origin_uuid": str(upstream_node.uuid),
                "target_uuid": str(local_node.uuid),
                "channel": "stable",
            }
        },
        sender=upstream_node,
    )

    assert request is not None
    assert request.status == RemoteUpgradeRequest.Status.REJECTED
    assert "disabled" in request.rejection_reason
    assert responses == [(RemoteUpgradeRequest.Status.REJECTED, upstream_node.pk)]


@pytest.mark.django_db
def test_receive_remote_upgrade_request_accepts_satellite_role_by_default(
    monkeypatch, settings, local_node, upstream_node
):
    trigger_calls: list[str | None] = []
    responses: list[str] = []
    settings.NODE_ROLE = "Terminal"
    satellite_role = NodeRole.objects.create(name="Satellite", acronym="STLT")
    local_node.role = satellite_role
    local_node.save(update_fields=["role"])
    monkeypatch.delenv(remote_upgrade.REMOTE_UPGRADE_ENV, raising=False)
    monkeypatch.setattr(
        remote_upgrade,
        "is_suite_feature_enabled",
        lambda slug, default=True: slug == remote_upgrade.REMOTE_UPGRADE_FEATURE_SLUG,
    )
    monkeypatch.setattr(
        remote_upgrade,
        "_trigger_upgrade_check",
        lambda channel_override=None: trigger_calls.append(channel_override) or True,
    )
    monkeypatch.setattr(
        remote_upgrade,
        "_send_remote_upgrade_response",
        lambda request, target: responses.append(request.status),
    )

    request = remote_upgrade.receive_remote_upgrade_request(
        {
            "remote_upgrade_request": {
                "uuid": "12121212-1212-4212-8212-121212121212",
                "origin_uuid": str(upstream_node.uuid),
                "target_uuid": str(local_node.uuid),
                "channel": "stable",
            }
        },
        sender=upstream_node,
    )

    assert request is not None
    assert request.status == RemoteUpgradeRequest.Status.QUEUED
    assert request.rejection_reason == ""
    assert trigger_calls == [None]
    assert responses == [RemoteUpgradeRequest.Status.QUEUED]


@pytest.mark.django_db
def test_receive_remote_upgrade_request_env_false_disables_satellite(
    monkeypatch, local_node, upstream_node
):
    trigger_calls: list[str | None] = []
    satellite_role = NodeRole.objects.create(name="Satellite", acronym="STLT")
    local_node.role = satellite_role
    local_node.save(update_fields=["role"])
    monkeypatch.setenv(remote_upgrade.REMOTE_UPGRADE_ENV, "0")
    monkeypatch.setattr(
        remote_upgrade,
        "is_suite_feature_enabled",
        lambda slug, default=True: slug == remote_upgrade.REMOTE_UPGRADE_FEATURE_SLUG,
    )
    monkeypatch.setattr(
        remote_upgrade,
        "_trigger_upgrade_check",
        lambda channel_override=None: trigger_calls.append(channel_override) or True,
    )
    monkeypatch.setattr(remote_upgrade, "_send_remote_upgrade_response", lambda *args, **kwargs: None)

    request = remote_upgrade.receive_remote_upgrade_request(
        {
            "remote_upgrade_request": {
                "uuid": "13131313-1313-4313-8313-131313131313",
                "origin_uuid": str(upstream_node.uuid),
                "target_uuid": str(local_node.uuid),
                "channel": "stable",
            }
        },
        sender=upstream_node,
    )

    assert request is not None
    assert request.status == RemoteUpgradeRequest.Status.REJECTED
    assert "disabled" in request.rejection_reason
    assert trigger_calls == []


@pytest.mark.django_db
def test_receive_remote_upgrade_request_accepts_once_when_opted_in(
    monkeypatch, local_node, upstream_node
):
    trigger_calls: list[str | None] = []
    responses: list[str] = []
    monkeypatch.setenv(remote_upgrade.REMOTE_UPGRADE_ENV, "1")
    monkeypatch.setattr(
        remote_upgrade,
        "is_suite_feature_enabled",
        lambda slug, default=False: slug == remote_upgrade.REMOTE_UPGRADE_FEATURE_SLUG,
    )
    monkeypatch.setattr(
        remote_upgrade,
        "_trigger_upgrade_check",
        lambda channel_override=None: trigger_calls.append(channel_override) or True,
    )
    monkeypatch.setattr(
        remote_upgrade,
        "_send_remote_upgrade_response",
        lambda request, target: responses.append(request.status),
    )

    payload = {
        "remote_upgrade_request": {
            "uuid": "22222222-2222-4222-8222-222222222222",
            "origin_uuid": str(upstream_node.uuid),
            "target_uuid": str(local_node.uuid),
            "channel": "regular",
            "reason": "maintenance",
        }
    }

    first = remote_upgrade.receive_remote_upgrade_request(payload, sender=upstream_node)
    second = remote_upgrade.receive_remote_upgrade_request(payload, sender=upstream_node)

    assert first is not None
    assert second is not None
    assert first.pk == second.pk
    assert first.status == RemoteUpgradeRequest.Status.QUEUED
    assert first.trigger_result == "queued"
    assert trigger_calls == ["regular"]
    assert responses == [
        RemoteUpgradeRequest.Status.QUEUED,
        RemoteUpgradeRequest.Status.QUEUED,
    ]


@pytest.mark.django_db
def test_receive_remote_upgrade_request_rejects_wrong_target(
    monkeypatch, local_node, upstream_node
):
    monkeypatch.setenv(remote_upgrade.REMOTE_UPGRADE_ENV, "1")
    monkeypatch.setattr(
        remote_upgrade,
        "is_suite_feature_enabled",
        lambda slug, default=False: True,
    )
    monkeypatch.setattr(remote_upgrade, "_send_remote_upgrade_response", lambda *args, **kwargs: None)

    request = remote_upgrade.receive_remote_upgrade_request(
        {
            "remote_upgrade_request": {
                "uuid": "33333333-3333-4333-8333-333333333333",
                "origin_uuid": str(upstream_node.uuid),
                "target_uuid": "44444444-4444-4444-8444-444444444444",
                "channel": "stable",
            }
        },
        sender=upstream_node,
    )

    assert request is not None
    assert request.status == RemoteUpgradeRequest.Status.REJECTED
    assert "target" in request.rejection_reason


@pytest.mark.django_db
def test_inbound_remote_upgrade_request_is_not_forwarded_after_wrong_target(
    monkeypatch, local_node, upstream_node, downstream_node
):
    propagated: list[NetMessage] = []
    monkeypatch.setenv(remote_upgrade.REMOTE_UPGRADE_ENV, "1")
    monkeypatch.setattr(
        remote_upgrade,
        "is_suite_feature_enabled",
        lambda slug, default=False: True,
    )
    monkeypatch.setattr(
        remote_upgrade,
        "_send_remote_upgrade_response",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        NetMessage,
        "propagate",
        lambda self, seen=None, **kwargs: propagated.append(self),
    )

    msg = NetMessage.receive_payload(
        {
            "uuid": "77777777-7777-4777-8777-777777777777",
            "subject": "Remote upgrade request",
            "body": "stable",
            "kind": NetMessage.Kind.REMOTE_UPGRADE_REQUEST,
            "control_payload": {
                "remote_upgrade_request": {
                    "uuid": "88888888-8888-4888-8888-888888888888",
                    "origin_uuid": str(upstream_node.uuid),
                    "target_uuid": str(downstream_node.uuid),
                    "channel": "stable",
                }
            },
            "filter_node": str(downstream_node.uuid),
        },
        sender=upstream_node,
    )

    request = RemoteUpgradeRequest.objects.get(
        uuid="88888888-8888-4888-8888-888888888888"
    )
    assert msg.kind == NetMessage.Kind.REMOTE_UPGRADE_REQUEST
    assert request.status == RemoteUpgradeRequest.Status.REJECTED
    assert "target" in request.rejection_reason
    assert propagated == []


@pytest.mark.django_db
def test_inbound_remote_upgrade_request_is_not_forwarded_after_acceptance(
    monkeypatch, local_node, upstream_node
):
    propagated: list[NetMessage] = []
    monkeypatch.setenv(remote_upgrade.REMOTE_UPGRADE_ENV, "1")
    monkeypatch.setattr(
        remote_upgrade,
        "is_suite_feature_enabled",
        lambda slug, default=False: True,
    )
    monkeypatch.setattr(
        remote_upgrade,
        "_trigger_upgrade_check",
        lambda channel_override=None: True,
    )
    monkeypatch.setattr(
        remote_upgrade,
        "_send_remote_upgrade_response",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        NetMessage,
        "propagate",
        lambda self, seen=None, **kwargs: propagated.append(self),
    )

    NetMessage.receive_payload(
        {
            "uuid": "99999999-9999-4999-8999-999999999999",
            "subject": "Remote upgrade request",
            "body": "stable",
            "kind": NetMessage.Kind.REMOTE_UPGRADE_REQUEST,
            "control_payload": {
                "remote_upgrade_request": {
                    "uuid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    "origin_uuid": str(upstream_node.uuid),
                    "target_uuid": str(local_node.uuid),
                    "channel": "stable",
                }
            },
            "filter_node": str(local_node.uuid),
        },
        sender=upstream_node,
    )

    request = RemoteUpgradeRequest.objects.get(
        uuid="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    )
    assert request.status == RemoteUpgradeRequest.Status.QUEUED
    assert propagated == []


@pytest.mark.django_db
@pytest.mark.parametrize(
    "kind",
    [
        NetMessage.Kind.REMOTE_UPGRADE_REQUEST,
        NetMessage.Kind.REMOTE_UPGRADE_RESPONSE,
    ],
)
@pytest.mark.parametrize("payload_fragment", [{}, {"control_payload": ["invalid"]}])
def test_inbound_remote_upgrade_control_message_without_valid_payload_is_not_forwarded(
    monkeypatch,
    upstream_node,
    kind,
    payload_fragment,
):
    propagated: list[NetMessage] = []
    monkeypatch.setattr(
        NetMessage,
        "propagate",
        lambda self, seen=None, **kwargs: propagated.append(self),
    )

    msg = NetMessage.receive_payload(
        {
            "uuid": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "subject": "Remote upgrade control",
            "body": "stable",
            "kind": kind,
            **payload_fragment,
        },
        sender=upstream_node,
    )

    assert msg.kind == kind
    msg.refresh_from_db()
    assert msg.complete is True
    assert propagated == []


@pytest.mark.django_db
@pytest.mark.parametrize(
    "kind",
    [
        NetMessage.Kind.REMOTE_UPGRADE_REQUEST,
        NetMessage.Kind.REMOTE_UPGRADE_RESPONSE,
    ],
)
def test_inbound_remote_upgrade_control_message_cannot_be_resent(
    monkeypatch,
    local_node,
    upstream_node,
    downstream_node,
    kind,
):
    sent_nodes: list[Node] = []
    monkeypatch.setattr(
        propagation,
        "send_net_message",
        lambda payload, node, **kwargs: sent_nodes.append(node) or True,
    )
    msg = NetMessage.objects.create(
        subject="Remote upgrade control",
        body="stable",
        kind=kind,
        control_payload={"remote_upgrade_request": {"uuid": str(upstream_node.uuid)}},
        node_origin=upstream_node,
        filter_node=downstream_node,
        target_limit=1,
        complete=False,
    )

    msg.propagate()

    msg.refresh_from_db()
    assert sent_nodes == []
    assert msg.complete is True


@pytest.mark.django_db
def test_inbound_remote_upgrade_control_spoofing_local_origin_cannot_be_resent(
    monkeypatch,
    local_node,
    upstream_node,
    downstream_node,
):
    sent_nodes: list[Node] = []
    monkeypatch.setattr(
        propagation,
        "send_net_message",
        lambda payload, node, **kwargs: sent_nodes.append(node) or True,
    )

    msg = NetMessage.receive_payload(
        {
            "uuid": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            "subject": "Remote upgrade control",
            "body": "stable",
            "kind": NetMessage.Kind.REMOTE_UPGRADE_REQUEST,
            "origin": str(local_node.uuid),
            "control_payload": {
                "remote_upgrade_request": {
                    "uuid": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                    "origin_uuid": str(upstream_node.uuid),
                    "target_uuid": str(downstream_node.uuid),
                    "channel": "stable",
                }
            },
            "filter_node": str(downstream_node.uuid),
        },
        sender=upstream_node,
    )

    msg.refresh_from_db()
    assert msg.node_origin == upstream_node
    assert msg.complete is True
    assert sent_nodes == [upstream_node]

    sent_nodes.clear()
    msg.propagate()

    assert sent_nodes == []


@pytest.mark.django_db
def test_local_remote_upgrade_control_message_can_still_propagate(
    monkeypatch,
    local_node,
    downstream_node,
):
    sent_nodes: list[Node] = []
    monkeypatch.setattr(
        propagation,
        "send_net_message",
        lambda payload, node, **kwargs: sent_nodes.append(node) or True,
    )
    monkeypatch.setattr(
        "apps.core.notifications.notify",
        lambda *args, **kwargs: False,
    )
    msg = NetMessage.objects.create(
        subject="Remote upgrade request",
        body="stable",
        kind=NetMessage.Kind.REMOTE_UPGRADE_REQUEST,
        control_payload={"remote_upgrade_request": {"uuid": str(downstream_node.uuid)}},
        node_origin=local_node,
        filter_node=downstream_node,
        target_limit=1,
        complete=False,
    )

    msg.propagate(allow_remote_upgrade_control=True)

    msg.refresh_from_db()
    assert sent_nodes == [downstream_node]
    assert msg.complete is True


@pytest.mark.django_db
def test_local_remote_upgrade_control_queue_sets_local_delivery_flag(
    monkeypatch,
    local_node,
    downstream_node,
):
    monkeypatch.setattr(
        propagation,
        "send_net_message",
        lambda payload, node, **kwargs: False,
    )
    monkeypatch.setattr(
        "apps.core.notifications.notify",
        lambda *args, **kwargs: False,
    )
    msg = NetMessage.objects.create(
        subject="Remote upgrade request",
        body="stable",
        kind=NetMessage.Kind.REMOTE_UPGRADE_REQUEST,
        control_payload={"remote_upgrade_request": {"uuid": str(downstream_node.uuid)}},
        node_origin=local_node,
        filter_node=downstream_node,
        target_limit=1,
        complete=False,
    )

    msg.propagate(allow_remote_upgrade_control=True)

    pending = PendingNetMessage.objects.get(message=msg, node=downstream_node)
    assert pending.local_remote_upgrade_control is True


@pytest.mark.django_db
def test_receive_remote_upgrade_request_rejects_expired_payload(
    monkeypatch, local_node, upstream_node
):
    trigger_calls: list[str | None] = []
    responses: list[str] = []
    monkeypatch.setenv(remote_upgrade.REMOTE_UPGRADE_ENV, "1")
    monkeypatch.setattr(
        remote_upgrade,
        "is_suite_feature_enabled",
        lambda slug, default=False: True,
    )
    monkeypatch.setattr(
        remote_upgrade,
        "_trigger_upgrade_check",
        lambda channel_override=None: trigger_calls.append(channel_override) or True,
    )
    monkeypatch.setattr(
        remote_upgrade,
        "_send_remote_upgrade_response",
        lambda request, target: responses.append(request.status),
    )

    request = remote_upgrade.receive_remote_upgrade_request(
        {
            "remote_upgrade_request": {
                "uuid": "55555555-5555-4555-8555-555555555555",
                "origin_uuid": str(upstream_node.uuid),
                "target_uuid": str(local_node.uuid),
                "channel": "stable",
                "expires_at": (timezone.now() - timedelta(minutes=5)).isoformat(),
            }
        },
        sender=upstream_node,
    )

    assert request is not None
    assert request.status == RemoteUpgradeRequest.Status.REJECTED
    assert "expired" in request.rejection_reason.lower()
    assert trigger_calls == []
    assert responses == [RemoteUpgradeRequest.Status.REJECTED]


@pytest.mark.django_db
def test_receive_remote_upgrade_request_rejects_invalid_channel(
    monkeypatch, local_node, upstream_node
):
    trigger_calls: list[str | None] = []
    responses: list[str] = []
    monkeypatch.setattr(
        remote_upgrade,
        "_trigger_upgrade_check",
        lambda channel_override=None: trigger_calls.append(channel_override) or True,
    )
    monkeypatch.setattr(
        remote_upgrade,
        "_send_remote_upgrade_response",
        lambda request, target: responses.append(request.status),
    )

    request = remote_upgrade.receive_remote_upgrade_request(
        {
            "remote_upgrade_request": {
                "uuid": "66666666-6666-4666-8666-666666666666",
                "origin_uuid": str(upstream_node.uuid),
                "target_uuid": str(local_node.uuid),
                "channel": "bogus",
            }
        },
        sender=upstream_node,
    )

    assert request is not None
    assert request.status == RemoteUpgradeRequest.Status.REJECTED
    assert "unsupported upgrade channel" in request.rejection_reason.lower()
    assert trigger_calls == []
    assert responses == [RemoteUpgradeRequest.Status.REJECTED]


@pytest.mark.django_db
def test_receive_remote_upgrade_response_updates_origin_record(local_node, downstream_node):
    request = RemoteUpgradeRequest.objects.create(
        origin_node=local_node,
        target_node=downstream_node,
        origin_uuid=local_node.uuid,
        target_uuid=downstream_node.uuid,
        channel="stable",
    )

    remote_upgrade.receive_remote_upgrade_response(
        {
            "remote_upgrade_response": {
                "uuid": str(request.uuid),
                "status": RemoteUpgradeRequest.Status.REJECTED,
                "rejection_reason": "Channel not allowed",
                "trigger_result": "",
            }
        },
        sender=downstream_node,
    )

    request.refresh_from_db()
    assert request.status == RemoteUpgradeRequest.Status.REJECTED
    assert request.rejection_reason == "Channel not allowed"
    assert request.responded_at is not None


@pytest.mark.django_db
def test_node_upgrade_request_command_outputs_json(
    monkeypatch, local_node, downstream_node
):
    monkeypatch.setattr(NetMessage, "propagate", lambda self, seen=None, **kwargs: None)

    stdout = StringIO()
    call_command(
        "node",
        "upgrade-request",
        "--node",
        str(downstream_node.pk),
        "--channel",
        "stable",
        "--json",
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert payload["target_id"] == downstream_node.pk
    assert payload["channel"] == "stable"
    assert payload["status"] == RemoteUpgradeRequest.Status.REQUESTED
