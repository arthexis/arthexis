from __future__ import annotations

import json

import pytest
from django.contrib.sites.models import Site
from django.urls import reverse

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


@pytest.mark.django_db
def test_whatsapp_webhook_stores_message_fields(client):
    site = Site.objects.create(domain="example.test", name="example")
    bridge = WhatsAppChatBridge.objects.create(
        site=site,
        phone_number_id="12345",
        access_token="token",
    )
    webhook = WhatsAppWebhook.objects.create(
        bridge=bridge,
        route_key="route-key-2",
        verify_token="verify-token-2",
    )

    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15551234567",
                                "phone_number_id": "12345",
                            },
                            "contacts": [
                                {
                                    "profile": {"name": "Alice"},
                                    "wa_id": "15557654321",
                                }
                            ],
                            "messages": [
                                {
                                    "from": "15557654321",
                                    "id": "wamid.ABC",
                                    "timestamp": "1700000000",
                                    "type": "text",
                                    "text": {"body": "hello"},
                                    "context": {"id": "wamid.PREV"},
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    }

    response = client.post(
        reverse("meta:whatsapp-webhook", kwargs={"route_key": webhook.route_key}),
        data=json.dumps(payload),
        content_type="application/json",
    )

    assert response.status_code == 200
    message = WhatsAppWebhookMessage.objects.get(webhook=webhook)
    assert message.message_id == "wamid.ABC"
    assert message.messaging_product == "whatsapp"
    assert message.from_phone == "15557654321"
    assert message.wa_id == "15557654321"
    assert message.profile_name == "Alice"
    assert message.timestamp == 1700000000
    assert message.message_type == "text"
    assert message.text_body == "hello"
    assert message.context_message_id == "wamid.PREV"
    assert message.metadata_phone_number_id == "12345"
    assert message.metadata_display_phone_number == "15551234567"


@pytest.mark.django_db
def test_whatsapp_webhook_updates_existing_message(client):
    site = Site.objects.create(domain="example.test", name="example")
    bridge = WhatsAppChatBridge.objects.create(
        site=site,
        phone_number_id="12345",
        access_token="token",
    )
    webhook = WhatsAppWebhook.objects.create(
        bridge=bridge,
        route_key="route-key-3",
        verify_token="verify-token-3",
    )

    url = reverse("meta:whatsapp-webhook", kwargs={"route_key": webhook.route_key})
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid.SAME",
                                    "type": "text",
                                    "text": {"body": "first"},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }

    first = client.post(url, data=json.dumps(payload), content_type="application/json")
    assert first.status_code == 200

    payload["entry"][0]["changes"][0]["value"]["messages"][0]["text"]["body"] = "updated"
    second = client.post(url, data=json.dumps(payload), content_type="application/json")
    assert second.status_code == 200

    assert WhatsAppWebhookMessage.objects.count() == 1
    assert WhatsAppWebhookMessage.objects.get().text_body == "updated"


@pytest.mark.django_db
def test_whatsapp_webhook_ignores_non_list_contacts(client):
    site = Site.objects.create(domain="example.test", name="example")
    bridge = WhatsAppChatBridge.objects.create(
        site=site,
        phone_number_id="12345",
        access_token="token",
    )
    webhook = WhatsAppWebhook.objects.create(
        bridge=bridge,
        route_key="route-key-4",
        verify_token="verify-token-4",
    )

    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": "malformed",
                            "messages": [
                                {
                                    "id": "wamid.NONLIST",
                                    "type": "text",
                                    "text": {"body": "hello"},
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    }

    response = client.post(
        reverse("meta:whatsapp-webhook", kwargs={"route_key": webhook.route_key}),
        data=json.dumps(payload),
        content_type="application/json",
    )

    assert response.status_code == 200
    message = WhatsAppWebhookMessage.objects.get(webhook=webhook, message_id="wamid.NONLIST")
    assert message.wa_id == ""
