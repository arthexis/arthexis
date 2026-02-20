"""Views for Meta/WhatsApp webhook endpoints."""

from __future__ import annotations

import json
import hmac
from hashlib import sha256
from typing import Any

from django.conf import settings
from django.db import transaction
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
            for index, message in enumerate(messages):
                if not isinstance(message, dict):
                    continue
                contact = contacts[index] if isinstance(contacts, list) and index < len(contacts) else {}
                if not isinstance(contact, dict):
                    contact = {}
                profile = contact.get("profile") if isinstance(contact.get("profile"), dict) else {}
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
        if mode == "subscribe" and hmac.compare_digest(token or "", webhook.verify_token or ""):
            return HttpResponse(challenge or "ok", content_type="text/plain")
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

    app_secret = getattr(settings, "PAGES_WHATSAPP_APP_SECRET", "")
    if app_secret:
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not signature.startswith("sha256="):
            return HttpResponse("Unauthorized", status=401)
        expected = hmac.new(app_secret.encode("utf-8"), request.body, sha256).hexdigest()
        if not hmac.compare_digest(signature[7:], expected):
            return HttpResponse("Unauthorized", status=401)

    with transaction.atomic():
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
