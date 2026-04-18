from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from django.contrib.sites.models import Site
from django.test import override_settings
from django.urls import reverse

from apps.features.models import Feature
from apps.meta.models import WhatsAppChatBridge, WhatsAppWebhook, WhatsAppWebhookMessage

@pytest.mark.django_db
def test_whatsapp_webhook_verification(client):
    site = Site.objects.create(domain="example.test", name="example")
    bridge = WhatsAppChatBridge.objects.create(
        site=site,
        phone_number_id="12345",
        access_token="token",
        is_default=False,
    )
    webhook = WhatsAppWebhook.objects.create(
        bridge=bridge,
        route_key="route-key",
        verify_token="verify-token",
    )

    response = client.get(
        reverse("meta:whatsapp-webhook", kwargs={"route_key": webhook.route_key}),
        {
            "hub.mode": "subscribe",
            "hub.verify_token": webhook.verify_token,
            "hub.challenge": "abc123",
        },
    )

    assert response.status_code == 200
    assert response.content.decode() == "abc123"
    assert response["Content-Type"].startswith("text/plain")


@pytest.mark.django_db
def test_whatsapp_webhook_disabled_feature_still_stores_messages_for_audit(client):
    """Disabled WhatsApp suite feature should keep audit storage but stop bridge activity."""

    Feature.objects.update_or_create(
        slug="whatsapp-chat-bridge",
        defaults={"display": "WhatsApp Chat Bridge", "is_enabled": False},
    )
    site = Site.objects.create(domain="example.test", name="example")
    bridge = WhatsAppChatBridge.objects.create(
        site=site,
        phone_number_id="12345",
        access_token="token",
    )
    webhook = WhatsAppWebhook.objects.create(
        bridge=bridge,
        route_key="route-key-disabled",
        verify_token="verify-token-disabled",
    )

    payload = {
        "entry": [{"changes": [{"value": {"messages": [{"id": "wamid.DISABLED", "type": "text"}]}}]}]
    }
    response = client.post(
        reverse("meta:whatsapp-webhook", kwargs={"route_key": webhook.route_key}),
        data=json.dumps(payload),
        content_type="application/json",
    )

    assert response.status_code == 202
    assert (
        WhatsAppWebhookMessage.objects.filter(
            webhook=webhook,
            message_id="wamid.DISABLED",
        ).exists()
    )
