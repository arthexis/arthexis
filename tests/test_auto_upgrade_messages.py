from __future__ import annotations

from datetime import datetime

import pytest
from django.utils import timezone

from apps.core import tasks
from apps.core.notifications import LcdChannel


@pytest.mark.django_db
def test_send_auto_upgrade_check_message(monkeypatch):
    sent = []
    fixed_now = timezone.make_aware(datetime(2024, 1, 1, 12, 34))
    monkeypatch.setattr(tasks.timezone, "now", lambda: fixed_now)

    def fake_broadcast(
        cls,
        subject,
        body,
        reach=None,
        seen=None,
        attachments=None,
        **kwargs,
    ):
        sent.append(
            {
                "subject": subject,
                "body": body,
                "lcd_channel_type": kwargs.get("lcd_channel_type"),
                "lcd_channel_num": kwargs.get("lcd_channel_num"),
            }
        )

    from apps.nodes.models.node_core import NetMessage

    monkeypatch.setattr(NetMessage, "broadcast", classmethod(fake_broadcast))

    tasks._send_auto_upgrade_check_message("APPLIED-SUCCESSFULLY", "CLEAN")

    assert sent[0]["subject"] == "UP-CHECK 12:34"
    assert sent[0]["body"] == "APPLIED-SUCCESSF CLEAN"
    assert sent[0]["lcd_channel_type"] == LcdChannel.HIGH.value
    assert sent[0]["lcd_channel_num"] == 1


@pytest.mark.parametrize(
    "current_version, target_version, current_rev, target_rev, expected",
    [
        pytest.param("1.0", "2.0", "aaa", "aaa", "2.0", id="version-change"),
        pytest.param("1.0", "1.0", "abc", "1234567", "234567", id="revision-change"),
        pytest.param("1.0", "1.0", "aaa", "aaa", "CLEAN", id="no-change"),
        pytest.param("1.0", None, "aaa", "aaa", "-", id="missing-version"),
    ],
)
def test_resolve_auto_upgrade_change_tag_cases(
    current_version, target_version, current_rev, target_rev, expected
):
    assert (
        tasks._resolve_auto_upgrade_change_tag(
            current_version, target_version, current_rev, target_rev
        )
        == expected
    )
