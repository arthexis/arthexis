import base64
import json
import logging
from collections.abc import Mapping

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt

from apps.ocpp.models import Charger
from apps.ocpp.network import (
    apply_remote_charger_payload,
    serialize_charger_for_network,
    sync_transactions_payload,
)
from apps.ocpp.transactions_io import export_transactions

from ..models import NetMessage, Node, PendingNetMessage

logger = logging.getLogger("apps.nodes.views")


def _load_signed_node(
    request,
    requester_id: str,
    *,
    mac_address: str | None = None,
    public_key: str | None = None,
):
    signature = request.headers.get("X-Signature")
    if not signature:
        return None, JsonResponse({"detail": "signature required"}, status=403)
    try:
        signature_bytes = base64.b64decode(signature)
    except Exception:
        return None, JsonResponse({"detail": "invalid signature"}, status=403)

    candidates: list[Node] = []
    seen: set[int] = set()

    lookup_values: list[tuple[str, str]] = []
    if requester_id:
        lookup_values.append(("uuid", requester_id))
    if mac_address:
        lookup_values.append(("mac_address__iexact", mac_address))
    if public_key:
        lookup_values.append(("public_key", public_key))

    for field, value in lookup_values:
        node = Node.objects.filter(**{field: value}).first()
        if not node or not node.public_key:
            continue
        if node.pk is not None and node.pk in seen:
            continue
        if node.pk is not None:
            seen.add(node.pk)
        candidates.append(node)

    if not candidates:
        return None, JsonResponse({"detail": "unknown requester"}, status=403)

    for node in candidates:
        try:
            loaded_key = serialization.load_pem_public_key(node.public_key.encode())
            loaded_key.verify(
                signature_bytes,
                request.body,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH,
                ),
                hashes.SHA256(),
            )
        except Exception:
            continue
        return node, None

    return None, JsonResponse({"detail": "invalid signature"}, status=403)


def _clean_requester_hint(value, *, strip: bool = True) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip() if strip else value
    if not cleaned:
        return None
    return cleaned


def _normalize_requested_chargers(values) -> list[tuple[str, int | None, object]]:
    if not isinstance(values, list):
        return []

    normalized: list[tuple[str, int | None, object]] = []
    for entry in values:
        if not isinstance(entry, Mapping):
            continue
        serial = Charger.normalize_serial(entry.get("charger_id"))
        if not serial or Charger.is_placeholder_serial(serial):
            continue
        connector = entry.get("connector_id")
        if connector in ("", None):
            connector_value = None
        elif isinstance(connector, int):
            connector_value = connector
        else:
            try:
                connector_value = int(str(connector))
            except (TypeError, ValueError):
                connector_value = None
        since_raw = entry.get("since")
        since_dt = None
        if isinstance(since_raw, str):
            since_dt = parse_datetime(since_raw)
            if since_dt is not None and timezone.is_naive(since_dt):
                since_dt = timezone.make_aware(since_dt, timezone.get_current_timezone())
        normalized.append((serial, connector_value, since_dt))
    return normalized


@csrf_exempt
def network_chargers(request):
    """Return serialized charger information for trusted peers."""

    if request.method != "POST":
        return JsonResponse({"detail": "POST required"}, status=405)

    try:
        body = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "invalid json"}, status=400)

    requester = body.get("requester")
    if not requester:
        return JsonResponse({"detail": "requester required"}, status=400)

    requester_mac = _clean_requester_hint(body.get("requester_mac"))
    requester_public_key = _clean_requester_hint(
        body.get("requester_public_key"), strip=False
    )

    node, error_response = _load_signed_node(
        request,
        requester,
        mac_address=requester_mac,
        public_key=requester_public_key,
    )
    if error_response is not None:
        return error_response

    requested = _normalize_requested_chargers(body.get("chargers") or [])

    qs = Charger.objects.all()
    local_node = Node.get_local()
    if local_node:
        qs = qs.filter(Q(node_origin=local_node) | Q(node_origin__isnull=True))

    if requested:
        filters = Q()
        for serial, connector_value, _ in requested:
            if connector_value is None:
                filters |= Q(charger_id=serial, connector_id__isnull=True)
            else:
                filters |= Q(charger_id=serial, connector_id=connector_value)
        qs = qs.filter(filters)

    chargers = [serialize_charger_for_network(charger) for charger in qs]

    include_transactions = bool(body.get("include_transactions"))
    response_data: dict[str, object] = {"chargers": chargers}

    if include_transactions:
        serials = [serial for serial, _, _ in requested] or list(
            {charger["charger_id"] for charger in chargers}
        )
        since_values = [since for _, _, since in requested if since]
        start = min(since_values) if since_values else None
        tx_payload = export_transactions(start=start, chargers=serials or None)
        response_data["transactions"] = tx_payload

    return JsonResponse(response_data)


@csrf_exempt
def forward_chargers(request):
    """Receive forwarded charger metadata and transactions from trusted peers."""

    if request.method != "POST":
        return JsonResponse({"detail": "POST required"}, status=405)

    try:
        body = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "invalid json"}, status=400)

    requester = body.get("requester")
    if not requester:
        return JsonResponse({"detail": "requester required"}, status=400)

    requester_mac = _clean_requester_hint(body.get("requester_mac"))
    requester_public_key = _clean_requester_hint(
        body.get("requester_public_key"), strip=False
    )

    node, error_response = _load_signed_node(
        request,
        requester,
        mac_address=requester_mac,
        public_key=requester_public_key,
    )
    if error_response is not None:
        return error_response

    processed = 0
    chargers_payload = body.get("chargers", [])
    if not isinstance(chargers_payload, list):
        chargers_payload = []
    for entry in chargers_payload:
        if not isinstance(entry, Mapping):
            continue
        charger = apply_remote_charger_payload(node, entry)
        if charger:
            processed += 1

    imported = 0
    transactions_payload = body.get("transactions")
    if isinstance(transactions_payload, Mapping):
        imported = sync_transactions_payload(transactions_payload)

    return JsonResponse({"status": "ok", "chargers": processed, "transactions": imported})


@csrf_exempt
def net_message(request):
    """Receive a network message and continue propagation."""

    if request.method != "POST":
        return JsonResponse({"detail": "POST required"}, status=400)
    try:
        data = json.loads(request.body.decode())
    except json.JSONDecodeError:
        return JsonResponse({"detail": "invalid json"}, status=400)

    signature = request.headers.get("X-Signature")
    sender_id = data.get("sender")
    if not signature or not sender_id:
        return JsonResponse({"detail": "signature required"}, status=403)
    node = Node.objects.filter(uuid=sender_id).first()
    if not node or not node.public_key:
        return JsonResponse({"detail": "unknown sender"}, status=403)
    try:
        public_key = serialization.load_pem_public_key(node.public_key.encode())
        public_key.verify(
            base64.b64decode(signature),
            request.body,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
    except Exception:
        return JsonResponse({"detail": "invalid signature"}, status=403)

    try:
        msg = NetMessage.receive_payload(data, sender=node)
    except ValueError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)
    return JsonResponse({"status": "propagated", "complete": msg.complete})


@csrf_exempt
def net_message_pull(request):
    """Allow downstream nodes to retrieve queued network messages."""

    if request.method != "POST":
        return JsonResponse({"detail": "POST required"}, status=405)
    try:
        data = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "invalid json"}, status=400)

    requester = data.get("requester")
    if not requester:
        return JsonResponse({"detail": "requester required"}, status=400)
    signature = request.headers.get("X-Signature")
    if not signature:
        return JsonResponse({"detail": "signature required"}, status=403)

    node = Node.objects.filter(uuid=requester).first()
    if not node or not node.public_key:
        return JsonResponse({"detail": "unknown requester"}, status=403)
    try:
        public_key = serialization.load_pem_public_key(node.public_key.encode())
        public_key.verify(
            base64.b64decode(signature),
            request.body,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
    except Exception:
        return JsonResponse({"detail": "invalid signature"}, status=403)

    local = Node.get_local()
    if not local:
        return JsonResponse({"detail": "local node unavailable"}, status=503)
    private_key = local.get_private_key()
    if not private_key:
        return JsonResponse({"detail": "signing unavailable"}, status=503)

    entries = (
        PendingNetMessage.objects.select_related(
            "message",
            "message__filter_node",
            "message__filter_node_feature",
            "message__filter_node_role",
            "message__node_origin",
        )
        .filter(node=node)
        .order_by("queued_at")
    )
    messages: list[dict[str, object]] = []
    expired_ids: list[int] = []
    delivered_ids: list[int] = []

    origin_fallback = str(local.uuid)

    for entry in entries:
        if entry.is_stale:
            expired_ids.append(entry.pk)
            continue
        message = entry.message
        if message.is_expired:
            expired_ids.append(entry.pk)
            if not message.complete:
                message.complete = True
                message.save(update_fields=["complete"])
            continue
        reach_source = message.filter_node_role or message.reach
        reach_name = reach_source.name if reach_source else None
        origin_node = message.node_origin
        origin_uuid = str(origin_node.uuid) if origin_node else origin_fallback
        sender_id = str(local.uuid)
        seen = [str(value) for value in entry.seen]
        payload = message._build_payload(
            sender_id=sender_id,
            origin_uuid=origin_uuid,
            reach_name=reach_name,
            seen=seen,
        )
        payload_json = message._serialize_payload(payload)
        payload_signature = message._sign_payload(payload_json, private_key)
        if not payload_signature:
            logger.warning(
                "Unable to sign queued NetMessage %s for node %s", message.pk, node.pk
            )
            continue
        messages.append({"payload": payload, "signature": payload_signature})
        delivered_ids.append(entry.pk)

    if expired_ids:
        PendingNetMessage.objects.filter(pk__in=expired_ids).delete()
    if delivered_ids:
        PendingNetMessage.objects.filter(pk__in=delivered_ids).delete()

    return JsonResponse({"messages": messages})
