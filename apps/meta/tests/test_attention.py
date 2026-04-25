from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from apps.meta.models import Attention


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
