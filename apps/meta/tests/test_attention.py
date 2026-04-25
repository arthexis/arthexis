from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.features.models import Feature
from apps.meta.management.commands import attention as attention_command
from apps.meta.models import Attention, WhatsAppChatBridge


@pytest.mark.django_db
def test_attention_command_creates_no_send_request():
    stdout = StringIO()

    call_command(
        "attention",
        "ask",
        "Continue with the risky step?",
        "--title",
        "Attention",
        "--agent",
        "Harbor",
        "--recipient",
        "15551234567",
        "--no-send",
        stdout=stdout,
    )

    attention = Attention.objects.get()
    assert attention.title == "Attention"
    assert attention.agent == "Harbor"
    assert attention.recipient == "15551234567"
    assert attention.status == Attention.Status.PENDING
    assert attention.key in stdout.getvalue()
    assert "sent=no" in stdout.getvalue()


@pytest.mark.django_db
def test_attention_send_returns_false_when_whatsapp_feature_disabled(monkeypatch):
    Feature.objects.update_or_create(
        slug="whatsapp-chat-bridge",
        defaults={"display": "WhatsApp Chat Bridge", "is_enabled": False},
    )
    bridge = WhatsAppChatBridge.objects.create(
        phone_number_id="12345",
        access_token="token",
        is_default=True,
    )
    attention = Attention.objects.create(
        bridge=bridge,
        recipient="15551234567",
        title="Attention",
        message="Continue?",
    )

    def fail_send(*args, **kwargs):
        raise AssertionError("send_message should not be called when feature is disabled")

    monkeypatch.setattr(bridge, "send_message", fail_send)

    assert attention.send() is False
    attention.refresh_from_db()
    assert attention.sent_at is None


@pytest.mark.django_db
def test_attention_send_delivers_when_whatsapp_feature_enabled(monkeypatch):
    Feature.objects.update_or_create(
        slug="whatsapp-chat-bridge",
        defaults={"display": "WhatsApp Chat Bridge", "is_enabled": True},
    )
    bridge = WhatsAppChatBridge.objects.create(
        phone_number_id="12345",
        access_token="token",
        is_default=True,
    )
    attention = Attention.objects.create(
        bridge=bridge,
        recipient="15551234567",
        title="Attention",
        message="Continue?",
    )
    calls = []

    def fake_send_message(**kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(bridge, "send_message", fake_send_message)

    assert attention.send() is True
    attention.refresh_from_db()
    assert attention.sent_at is not None
    assert calls == [
        {
            "recipient": "15551234567",
            "content": attention.notification_body(),
        }
    ]


@pytest.mark.django_db
def test_attention_command_wait_fails_fast_when_delivery_fails(monkeypatch):
    bridge = WhatsAppChatBridge.objects.create(
        phone_number_id="12345",
        access_token="token",
        is_default=True,
    )
    stdout = StringIO()

    monkeypatch.setattr(Attention, "send", lambda self: False)

    with pytest.raises(CommandError, match="was not delivered; refusing to wait"):
        call_command(
            "attention",
            "ask",
            "Continue with the risky step?",
            "--recipient",
            "15551234567",
            "--bridge",
            str(bridge.pk),
            "--wait",
            stdout=stdout,
        )

    attention = Attention.objects.get()
    assert attention.status == Attention.Status.PENDING
    assert f"attention={attention.key}" in stdout.getvalue()
    assert "sent=no" in stdout.getvalue()


@pytest.mark.django_db
def test_attention_command_wait_returns_when_delivery_succeeds(monkeypatch):
    bridge = WhatsAppChatBridge.objects.create(
        phone_number_id="12345",
        access_token="token",
        is_default=True,
    )
    stdout = StringIO()

    def fake_send(self):
        self.mark_responded(
            response_text=f"{self.key} approved",
            response_from_phone=self.recipient,
            response_payload={"source": "test"},
        )
        return True

    monkeypatch.setattr(Attention, "send", fake_send)
    monkeypatch.setattr(attention_command, "close_old_connections", lambda: None)

    call_command(
        "attention",
        "ask",
        "Continue with the risky step?",
        "--recipient",
        "15551234567",
        "--bridge",
        str(bridge.pk),
        "--wait",
        stdout=stdout,
    )

    attention = Attention.objects.get()
    assert attention.status == Attention.Status.RESPONDED
    assert "sent=yes" in stdout.getvalue()
    assert "response=approved" in stdout.getvalue()


@pytest.mark.django_db
def test_attention_command_records_manual_response():
    attention = Attention.objects.create(
        recipient="15551234567",
        title="Attention",
        message="Continue?",
    )
    stdout = StringIO()

    call_command(
        "attention",
        "respond",
        attention.key,
        f"{attention.key} approved",
        "--from-phone",
        "15551234567",
        stdout=stdout,
    )

    attention.refresh_from_db()
    assert attention.status == Attention.Status.RESPONDED
    assert attention.response_text == "approved"
    assert attention.response_from_phone == "15551234567"
    assert f"attention={attention.key}" in stdout.getvalue()


@pytest.mark.django_db
def test_attention_command_preserves_explicit_bridge_with_no_send():
    bridge = WhatsAppChatBridge.objects.create(
        phone_number_id="12345",
        access_token="token",
        is_default=True,
    )

    call_command(
        "attention",
        "ask",
        "Continue with the risky step?",
        "--recipient",
        "15551234567",
        "--bridge",
        str(bridge.pk),
        "--no-send",
    )

    attention = Attention.objects.get()
    assert attention.bridge == bridge
    assert attention.status == Attention.Status.PENDING


@pytest.mark.django_db
def test_attention_command_refuses_to_overwrite_response_without_force():
    attention = Attention.objects.create(
        recipient="15551234567",
        title="Attention",
        message="Continue?",
    )
    attention.mark_responded(
        response_text="approved", response_from_phone="15551234567"
    )

    with pytest.raises(CommandError, match="already has a response"):
        call_command(
            "attention",
            "respond",
            attention.key,
            "denied",
        )

    attention.refresh_from_db()
    assert attention.response_text == "approved"


@pytest.mark.django_db
def test_attention_command_force_overwrites_existing_response():
    attention = Attention.objects.create(
        recipient="15551234567",
        title="Attention",
        message="Continue?",
    )
    attention.mark_responded(
        response_text="approved", response_from_phone="15551234567"
    )

    call_command(
        "attention",
        "respond",
        attention.key,
        f"{attention.key} denied",
        "--force",
        "--from-phone",
        "15557654321",
    )

    attention.refresh_from_db()
    assert attention.response_text == "denied"
    assert attention.response_from_phone == "15557654321"
