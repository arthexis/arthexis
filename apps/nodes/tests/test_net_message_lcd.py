from datetime import timedelta

import pytest
from django.utils import timezone

from apps.nodes.models import NetMessage
from apps.nodes.services import propagation


@pytest.mark.django_db
def test_net_message_suppressed_lcd_channel_skips_local_notify(monkeypatch, settings):
    settings.NET_MESSAGE_DISABLE_PROPAGATION = True
    calls: list[dict[str, object]] = []

    def fake_notify(subject, body, **kwargs):
        calls.append({"subject": subject, "body": body, **kwargs})
        return True

    monkeypatch.setattr("apps.core.notifications.notify", fake_notify)
    suppressed = NetMessage.objects.create(
        subject="Silent",
        body="Do not display",
        lcd_channel_type=NetMessage.SUPPRESS_LCD_CHANNEL_TYPE,
    )
    visible = NetMessage.objects.create(
        subject="Visible",
        body="Display",
        lcd_channel_type="high",
        lcd_channel_num=2,
        expires_at=timezone.now() + timedelta(minutes=5),
    )

    propagation.propagate(suppressed)
    propagation.propagate(visible)

    assert calls == [
        {
            "subject": "Visible",
            "body": "Display",
            "expires_at": visible.expires_at,
            "channel_type": "high",
            "channel_num": 2,
        }
    ]
