from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from django.contrib.sites.models import Site
from django.test import override_settings
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
    assert response["Content-Type"].startswith("text/plain")


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


@pytest.mark.django_db
def test_whatsapp_webhook_contacts_align_with_messages_by_index(client):
    site = Site.objects.create(domain="example.test", name="example")
    bridge = WhatsAppChatBridge.objects.create(
        site=site,
        phone_number_id="12345",
        access_token="token",
    )
    webhook = WhatsAppWebhook.objects.create(
        bridge=bridge,
        route_key="route-key-5",
        verify_token="verify-token-5",
    )

    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [
                                {"profile": {"name": "Alice"}, "wa_id": "111"},
                                {"profile": {"name": "Bob"}, "wa_id": "222"},
                            ],
                            "messages": [
                                {"id": "wamid.ONE", "type": "text", "text": {"body": "hello"}},
                                {"id": "wamid.TWO", "type": "text", "text": {"body": "world"}},
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
    message_one = WhatsAppWebhookMessage.objects.get(webhook=webhook, message_id="wamid.ONE")
    message_two = WhatsAppWebhookMessage.objects.get(webhook=webhook, message_id="wamid.TWO")
    assert message_one.wa_id == "111"
    assert message_one.profile_name == "Alice"
    assert message_two.wa_id == "222"
    assert message_two.profile_name == "Bob"


@pytest.mark.django_db
@override_settings(PAGES_WHATSAPP_APP_SECRET="test-secret")
def test_whatsapp_webhook_rejects_invalid_signature(client):
    site = Site.objects.create(domain="example.test", name="example")
    bridge = WhatsAppChatBridge.objects.create(
        site=site,
        phone_number_id="12345",
        access_token="token",
    )
    webhook = WhatsAppWebhook.objects.create(
        bridge=bridge,
        route_key="route-key-6",
        verify_token="verify-token-6",
    )

    payload = {"entry": [{"changes": [{"value": {"messages": [{"id": "wamid.BADSIG"}]}}]}]}
    response = client.post(
        reverse("meta:whatsapp-webhook", kwargs={"route_key": webhook.route_key}),
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256="sha256=invalid",
    )

    assert response.status_code == 401


@pytest.mark.django_db
@override_settings(PAGES_WHATSAPP_APP_SECRET="test-secret")
def test_whatsapp_webhook_accepts_valid_signature(client):
    site = Site.objects.create(domain="example.test", name="example")
    bridge = WhatsAppChatBridge.objects.create(
        site=site,
        phone_number_id="12345",
        access_token="token",
    )
    webhook = WhatsAppWebhook.objects.create(
        bridge=bridge,
        route_key="route-key-7",
        verify_token="verify-token-7",
    )

    payload = {"entry": [{"changes": [{"value": {"messages": [{"id": "wamid.GOODSIG"}]}}]}]}
    raw_payload = json.dumps(payload).encode("utf-8")
    digest = hmac.new(b"test-secret", raw_payload, hashlib.sha256).hexdigest()

    response = client.post(
        reverse("meta:whatsapp-webhook", kwargs={"route_key": webhook.route_key}),
        data=raw_payload,
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=f"sha256={digest}",
    )

    assert response.status_code == 200
    assert WhatsAppWebhookMessage.objects.filter(webhook=webhook, message_id="wamid.GOODSIG").exists()
