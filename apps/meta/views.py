"""Views for Meta/WhatsApp webhook endpoints."""

from __future__ import annotations

import json
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt

from apps.meta.models import WhatsAppWebhook, WhatsAppWebhookMessage


def _iter_messages(payload: dict[str, Any]):
    """Yield normalized WhatsApp message payloads from webhook input."""

    for entry in payload.get("entry", []):
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes", []):
            if not isinstance(change, dict):
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            metadata = value.get("metadata")
            contacts = value.get("contacts")
            messages = value.get("messages")
            if not isinstance(messages, list):
                continue
            contact = contacts[0] if isinstance(contacts, list) and contacts else {}
            if not isinstance(contact, dict):
                contact = {}
            profile = contact.get("profile") if isinstance(contact.get("profile"), dict) else {}
            for message in messages:
                if not isinstance(message, dict):
                    continue
                yield (
                    value,
                    metadata if isinstance(metadata, dict) else {},
                    contact,
                    profile,
                    message,
                )


@csrf_exempt
def whatsapp_webhook(request: HttpRequest, route_key: str) -> HttpResponse:
    """Handle verification and message delivery callbacks from WhatsApp."""

    webhook = WhatsAppWebhook.objects.select_related("bridge", "bridge__site").filter(
        route_key=route_key
    ).first()
    if webhook is None:
        return HttpResponse("Not found", status=404)

    if request.method == "GET":
        mode = request.GET.get("hub.mode", "")
        token = request.GET.get("hub.verify_token", "")
        challenge = request.GET.get("hub.challenge", "")
        if mode == "subscribe" and token == webhook.verify_token:
            return HttpResponse(challenge or "ok")
        return HttpResponse("Unauthorized", status=403)

    if request.method != "POST":
        return HttpResponse("Method not allowed", status=405)

    payload = getattr(request, "json", None)
    if not isinstance(payload, dict):
        try:
            payload = json.loads(request.body or b"{}")
        except (ValueError, UnicodeDecodeError):
            payload = {}

    if not isinstance(payload, dict):
        payload = {}

    for value, metadata, contact, profile, message in _iter_messages(payload):
        message_id = str(message.get("id") or "").strip()
        if not message_id:
            continue

        text = message.get("text") if isinstance(message.get("text"), dict) else {}
        context = message.get("context") if isinstance(message.get("context"), dict) else {}
        WhatsAppWebhookMessage.objects.update_or_create(
            webhook=webhook,
            message_id=message_id,
            defaults={
                "messaging_product": str(value.get("messaging_product") or ""),
                "from_phone": str(message.get("from") or ""),
                "wa_id": str(contact.get("wa_id") or ""),
                "profile_name": str(profile.get("name") or ""),
                "timestamp": int(message.get("timestamp")) if str(message.get("timestamp") or "").isdigit() else None,
                "message_type": str(message.get("type") or ""),
                "text_body": str(text.get("body") or ""),
                "context_message_id": str(context.get("id") or ""),
                "metadata_phone_number_id": str(metadata.get("phone_number_id") or ""),
                "metadata_display_phone_number": str(metadata.get("display_phone_number") or ""),
                "payload": message,
            },
        )

    return HttpResponse("ok")
