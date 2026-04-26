from __future__ import annotations

import json

import pytest
from django.contrib.sites.models import Site
from django.urls import reverse

from apps.features.models import Feature
from apps.meta.models import Attention, WhatsAppChatBridge, WhatsAppWebhook, WhatsAppWebhookMessage


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
        "entry": [
            {
                "changes": [
                    {"value": {"messages": [{"id": "wamid.DISABLED", "type": "text"}]}}
                ]
            }
        ]
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


@pytest.mark.django_db
def test_whatsapp_webhook_captures_attention_response(client):
    site = Site.objects.create(domain="attention.example.test", name="attention")
    bridge = WhatsAppChatBridge.objects.create(
        site=site,
        phone_number_id="12345",
        access_token="token",
    )
    webhook = WhatsAppWebhook.objects.create(
        bridge=bridge,
        route_key="route-key-attention",
        verify_token="verify-token-attention",
    )
    attention = Attention.objects.create(
        bridge=bridge,
        recipient="15551234567",
        agent="Harbor",
        title="Attention",
        message="Continue?",
    )

    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "contacts": [{"wa_id": "15551234567", "profile": {"name": "Ops"}}],
                            "metadata": {"phone_number_id": "12345"},
                            "messages": [
                                {
                                    "id": "wamid.ATTENTION",
                                    "from": "15551234567",
                                    "timestamp": "1710000000",
                                    "type": "text",
                                    "text": {"body": f"{attention.key} approved"},
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
    attention.refresh_from_db()
    assert attention.status == Attention.Status.RESPONDED
    assert attention.response_text == "approved"
    assert attention.response_from_phone == "15551234567"
    assert attention.response_message == WhatsAppWebhookMessage.objects.get(
        webhook=webhook,
        message_id="wamid.ATTENTION",
    )


@pytest.mark.django_db
def test_whatsapp_webhook_does_not_capture_keyed_response_from_wrong_sender(client):
    site = Site.objects.create(domain="attention-wrong-sender.example.test", name="attention")
    bridge = WhatsAppChatBridge.objects.create(
        site=site,
        phone_number_id="12345",
        access_token="token",
    )
    webhook = WhatsAppWebhook.objects.create(
        bridge=bridge,
        route_key="route-key-attention-wrong-sender",
        verify_token="verify-token-attention-wrong-sender",
    )
    attention = Attention.objects.create(
        bridge=bridge,
        recipient="15551234567",
        title="Attention",
        message="Continue?",
    )

    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid.ATTENTION.WRONG.SENDER",
                                    "from": "15557654321",
                                    "type": "text",
                                    "text": {"body": f"{attention.key} approved"},
                                }
                            ]
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
    attention.refresh_from_db()
    assert attention.status == Attention.Status.PENDING
    assert attention.response_text == ""
    assert attention.response_from_phone == ""
    assert WhatsAppWebhookMessage.objects.filter(
        webhook=webhook,
        message_id="wamid.ATTENTION.WRONG.SENDER",
    ).exists()


@pytest.mark.django_db
def test_whatsapp_webhook_does_not_capture_keyed_response_across_bridges(client):
    site_a = Site.objects.create(domain="key-bridge-a.example.test", name="key bridge a")
    site_b = Site.objects.create(domain="key-bridge-b.example.test", name="key bridge b")
    bridge_a = WhatsAppChatBridge.objects.create(
        site=site_a,
        phone_number_id="12345",
        access_token="token-a",
    )
    bridge_b = WhatsAppChatBridge.objects.create(
        site=site_b,
        phone_number_id="67890",
        access_token="token-b",
    )
    webhook_b = WhatsAppWebhook.objects.create(
        bridge=bridge_b,
        route_key="route-key-cross-bridge-key",
        verify_token="verify-token-cross-bridge-key",
    )
    attention = Attention.objects.create(
        bridge=bridge_a,
        recipient="15551234567",
        title="Attention",
        message="Continue?",
    )
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"wa_id": "15551234567"}],
                            "messages": [
                                {
                                    "id": "wamid.CROSS.BRIDGE.KEY",
                                    "from": "15551234567",
                                    "type": "text",
                                    "text": {"body": f"{attention.key} approved"},
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    }

    response = client.post(
        reverse("meta:whatsapp-webhook", kwargs={"route_key": webhook_b.route_key}),
        data=json.dumps(payload),
        content_type="application/json",
    )

    assert response.status_code == 200
    attention.refresh_from_db()
    assert attention.status == Attention.Status.PENDING
    assert attention.response_text == ""
    assert WhatsAppWebhookMessage.objects.filter(
        webhook=webhook_b,
        message_id="wamid.CROSS.BRIDGE.KEY",
    ).exists()


@pytest.mark.django_db
def test_whatsapp_webhook_captures_phone_fallback_for_same_bridge(client):
    site = Site.objects.create(domain="same-bridge.example.test", name="same bridge")
    bridge = WhatsAppChatBridge.objects.create(
        site=site,
        phone_number_id="12345",
        access_token="token",
    )
    webhook = WhatsAppWebhook.objects.create(
        bridge=bridge,
        route_key="route-key-phone-fallback",
        verify_token="verify-token-phone-fallback",
    )
    attention = Attention.objects.create(
        bridge=bridge,
        recipient="15551234567",
        title="Attention",
        message="Continue?",
    )
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"wa_id": "15551234567"}],
                            "messages": [
                                {
                                    "id": "wamid.PHONE.FALLBACK",
                                    "from": "15551234567",
                                    "type": "text",
                                    "text": {"body": "approved"},
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
    attention.refresh_from_db()
    assert attention.status == Attention.Status.RESPONDED
    assert attention.response_text == "approved"
    assert attention.response_message == WhatsAppWebhookMessage.objects.get(
        webhook=webhook,
        message_id="wamid.PHONE.FALLBACK",
    )


@pytest.mark.django_db
def test_whatsapp_webhook_does_not_capture_phone_fallback_across_bridges(client):
    site_a = Site.objects.create(domain="bridge-a.example.test", name="bridge a")
    site_b = Site.objects.create(domain="bridge-b.example.test", name="bridge b")
    bridge_a = WhatsAppChatBridge.objects.create(
        site=site_a,
        phone_number_id="12345",
        access_token="token-a",
    )
    bridge_b = WhatsAppChatBridge.objects.create(
        site=site_b,
        phone_number_id="67890",
        access_token="token-b",
    )
    webhook_b = WhatsAppWebhook.objects.create(
        bridge=bridge_b,
        route_key="route-key-cross-bridge",
        verify_token="verify-token-cross-bridge",
    )
    attention = Attention.objects.create(
        bridge=bridge_a,
        recipient="15551234567",
        title="Attention",
        message="Continue?",
    )
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"wa_id": "15551234567"}],
                            "messages": [
                                {
                                    "id": "wamid.CROSS.BRIDGE",
                                    "from": "15551234567",
                                    "type": "text",
                                    "text": {"body": "approved"},
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    }

    response = client.post(
        reverse("meta:whatsapp-webhook", kwargs={"route_key": webhook_b.route_key}),
        data=json.dumps(payload),
        content_type="application/json",
    )

    assert response.status_code == 200
    attention.refresh_from_db()
    assert attention.status == Attention.Status.PENDING
    assert attention.response_text == ""
    assert WhatsAppWebhookMessage.objects.filter(
        webhook=webhook_b,
        message_id="wamid.CROSS.BRIDGE",
    ).exists()


@pytest.mark.django_db
def test_whatsapp_webhook_disabled_feature_does_not_capture_attention_response(client):
    Feature.objects.update_or_create(
        slug="whatsapp-chat-bridge",
        defaults={"display": "WhatsApp Chat Bridge", "is_enabled": False},
    )
    site = Site.objects.create(domain="attention-disabled.example.test", name="attention disabled")
    bridge = WhatsAppChatBridge.objects.create(
        site=site,
        phone_number_id="12345",
        access_token="token",
    )
    webhook = WhatsAppWebhook.objects.create(
        bridge=bridge,
        route_key="route-key-attention-disabled",
        verify_token="verify-token-attention-disabled",
    )
    attention = Attention.objects.create(
        bridge=bridge,
        recipient="15551234567",
        title="Attention",
        message="Continue?",
    )
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid.ATTENTION.DISABLED",
                                    "from": "15551234567",
                                    "type": "text",
                                    "text": {"body": f"{attention.key} approved"},
                                }
                            ]
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

    assert response.status_code == 202
    attention.refresh_from_db()
    assert attention.status == Attention.Status.PENDING
