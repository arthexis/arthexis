"""NetMessage payload ingestion and propagation services."""

from __future__ import annotations

from datetime import timedelta
import logging
import random

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.nodes.models.node import Node
from apps.nodes.models.role import NodeRole
from apps.nodes.models.utils import _upgrade_in_progress
from apps.nodes.models.features import NodeFeature

logger = logging.getLogger(__name__)


def receive_payload(message_model, data: dict[str, object], *, sender: Node):
    """Create or update a net message from inbound payload data."""
    msg_uuid = data.get("uuid")
    if not msg_uuid:
        raise ValueError("uuid required")
    subject = (data.get("subject") or "")[:64]
    body = (data.get("body") or "")[:256]
    attachments = message_model.normalize_attachments(data.get("attachments"))
    reach_name = data.get("reach")
    reach_role = NodeRole.objects.filter(name=reach_name).first() if reach_name else None

    filter_node = Node.objects.filter(uuid=data.get("filter_node")).first() if data.get("filter_node") else None
    filter_feature = NodeFeature.objects.filter(slug=data.get("filter_node_feature")).first() if data.get("filter_node_feature") else None
    filter_role = NodeRole.objects.filter(name=data.get("filter_node_role")).first() if data.get("filter_node_role") else None

    filter_relation = ""
    if data.get("filter_current_relation"):
        relation = Node.normalize_relation(data.get("filter_current_relation"))
        filter_relation = relation.value if relation else ""

    filter_installed_version = (data.get("filter_installed_version") or "")[:20]
    filter_installed_revision = (data.get("filter_installed_revision") or "")[:40]
    seen_values = data.get("seen", [])
    if not isinstance(seen_values, list):
        seen_values = list(seen_values)
    normalized_seen = [str(v) for v in seen_values if v is not None]

    origin_node = Node.objects.filter(uuid=data.get("origin")).first() if data.get("origin") else None
    if not origin_node:
        origin_node = sender

    channel_type, channel_num = message_model.normalize_lcd_channel(data.get("lcd_channel_type"), data.get("lcd_channel_num"))
    expires_at = message_model.normalize_expires_at(data.get("expires_at"))

    msg, created = message_model.objects.get_or_create(
        uuid=msg_uuid,
        defaults={
            "subject": subject,
            "body": body,
            "reach": reach_role,
            "node_origin": origin_node,
            "attachments": attachments or None,
            "expires_at": expires_at,
            "lcd_channel_type": channel_type,
            "lcd_channel_num": channel_num,
            "filter_node": filter_node,
            "filter_node_feature": filter_feature,
            "filter_node_role": filter_role,
            "filter_current_relation": filter_relation,
            "filter_installed_version": filter_installed_version,
            "filter_installed_revision": filter_installed_revision,
        },
    )
    if not created:
        update_fields: list[str] = []
        for field, value in {
            "subject": subject,
            "body": body,
            "reach": reach_role,
            "expires_at": expires_at,
            "filter_node": filter_node,
            "filter_node_feature": filter_feature,
            "filter_node_role": filter_role,
            "filter_current_relation": filter_relation,
            "filter_installed_version": filter_installed_version,
            "filter_installed_revision": filter_installed_revision,
        }.items():
            if getattr(msg, field) != value:
                setattr(msg, field, value)
                update_fields.append(field)
        if msg.node_origin_id is None and origin_node:
            msg.node_origin = origin_node
            update_fields.append("node_origin")
        if attachments and msg.attachments != attachments:
            msg.attachments = attachments
            update_fields.append("attachments")
        if (msg.lcd_channel_type != channel_type) or (msg.lcd_channel_num != channel_num):
            msg.lcd_channel_type = channel_type
            msg.lcd_channel_num = channel_num
            update_fields.extend(["lcd_channel_type", "lcd_channel_num"])
        if update_fields:
            msg.save(update_fields=update_fields)

    if attachments:
        msg.apply_attachments(attachments)
    msg.propagate(seen=normalized_seen)
    return msg


def propagate(message, seen: list[str] | None = None) -> None:
    """Propagate ``message`` to eligible peers."""
    from apps.core.notifications import notify
    import requests
    from apps.nodes.models.net_message import PendingNetMessage

    if message.is_expired:
        if not message.complete:
            message.complete = True
            if message.pk:
                message.save(update_fields=["complete"])
        PendingNetMessage.objects.filter(message=message).delete()
        return

    channel_type, channel_num = message.normalize_lcd_channel(message.lcd_channel_type, message.lcd_channel_num)
    displayed = notify(message.subject, message.body, expires_at=message.expires_at, channel_type=channel_type, channel_num=channel_num)
    local = Node.get_local()
    if displayed:
        cutoff = timezone.now() - timedelta(hours=24)
        prune_qs = type(message).objects.filter(created__lt=cutoff)
        prune_qs = prune_qs.filter(models.Q(node_origin=local) | models.Q(node_origin__isnull=True)) if local else prune_qs.filter(node_origin__isnull=True)
        if message.pk:
            prune_qs = prune_qs.exclude(pk=message.pk)
        prune_qs.delete()

    if _upgrade_in_progress():
        logger.info("Skipping NetMessage propagation during upgrade in progress", extra={"id": message.pk})
        return
    if local and not message.node_origin_id:
        message.node_origin = local
        message.save(update_fields=["node_origin"])

    origin_uuid = str(message.node_origin.uuid) if message.node_origin_id else (str(local.uuid) if local else None)
    private_key = None
    seen = list(seen or [])
    local_id = None
    if local:
        local_id = str(local.uuid)
        if local_id not in seen:
            seen.append(local_id)
        private_key = local.get_private_key()
    for node_id in seen:
        node = Node.objects.filter(uuid=node_id).first()
        if node and (not local or node.pk != local.pk):
            message.propagated_to.add(node)

    if getattr(settings, "NET_MESSAGE_DISABLE_PROPAGATION", False):
        if not message.complete:
            message.complete = True
            if message.pk:
                message.save(update_fields=["complete"])
        return

    filtered_nodes = Node.objects.all()
    if message.filter_node_id:
        filtered_nodes = filtered_nodes.filter(pk=message.filter_node_id)
    if message.filter_node_feature_id:
        filtered_nodes = filtered_nodes.filter(features__pk=message.filter_node_feature_id)
    if message.filter_node_role_id:
        filtered_nodes = filtered_nodes.filter(role_id=message.filter_node_role_id)
    if message.filter_current_relation:
        filtered_nodes = filtered_nodes.filter(current_relation=message.filter_current_relation)
    if message.filter_installed_version:
        filtered_nodes = filtered_nodes.filter(installed_version=message.filter_installed_version)
    if message.filter_installed_revision:
        filtered_nodes = filtered_nodes.filter(installed_revision=message.filter_installed_revision)
    filtered_nodes = filtered_nodes.distinct()

    if local:
        filtered_nodes = filtered_nodes.exclude(pk=local.pk)
    total_known = filtered_nodes.count()
    remaining = list(filtered_nodes.exclude(pk__in=message.propagated_to.values_list("pk", flat=True)))
    if not remaining:
        message.complete = True
        message.save(update_fields=["complete"])
        return

    target_limit = min(message.target_limit or 6, len(remaining))
    reach_source = message.filter_node_role or message.reach
    reach_name = reach_source.name if reach_source else None
    role_map = {
        "Terminal": ["Terminal"],
        "Control": ["Control", "Terminal"],
        "Satellite": ["Satellite", "Control", "Terminal"],
        "Watchtower": ["Watchtower", "Satellite", "Control", "Terminal"],
        "Constellation": ["Watchtower", "Satellite", "Control", "Terminal"],
    }
    selected: list[Node] = []
    if message.filter_node_id:
        target = next((n for n in remaining if n.pk == message.filter_node_id), None)
        if target:
            selected = [target]
        else:
            message.complete = True
            message.save(update_fields=["complete"])
            return
    else:
        role_order = [reach_name] if message.filter_node_role_id else role_map.get(reach_name, [None])
        for role_name in role_order:
            role_nodes = remaining[:] if role_name is None else [n for n in remaining if n.role and n.role.name == role_name]
            random.shuffle(role_nodes)
            for n in role_nodes:
                selected.append(n)
                remaining.remove(n)
                if len(selected) >= target_limit:
                    break
            if len(selected) >= target_limit:
                break

    if not selected:
        message.complete = True
        message.save(update_fields=["complete"])
        return

    payload_seen = seen.copy() + [str(n.uuid) for n in selected]
    for node in selected:
        payload = message._build_payload(sender_id=local_id, origin_uuid=origin_uuid, reach_name=reach_name, seen=payload_seen)
        payload_json = message._serialize_payload(payload)
        headers = {"Content-Type": "application/json"}
        signature = message._sign_payload(payload_json, private_key)
        if signature:
            headers["X-Signature"] = signature
        success = False
        for url in node.iter_remote_urls("/nodes/net-message/"):
            try:
                response = requests.post(url, data=payload_json, headers=headers, timeout=1)
                success = bool(response.ok)
            except Exception:
                logger.exception("Failed to propagate NetMessage %s to node %s via %s", message.pk, node.pk, url)
                continue
            if success:
                break
        if success:
            message.clear_queue_for_node(node)
        else:
            message.queue_for_node(node, payload_seen)
        message.propagated_to.add(node)

    if total_known and message.propagated_to.count() >= total_known:
        message.complete = True
        message.save(update_fields=["complete"])
