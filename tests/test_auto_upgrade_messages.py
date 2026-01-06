from __future__ import annotations

from datetime import datetime

import pytest
from django.utils import timezone

from apps.core import tasks


@pytest.mark.django_db
def test_send_auto_upgrade_check_message(monkeypatch):
    sent = []
    fixed_now = timezone.make_aware(datetime(2024, 1, 1, 12, 34))
    monkeypatch.setattr(tasks.timezone, "now", lambda: fixed_now)

    def fake_broadcast(cls, subject, body, reach=None, seen=None, attachments=None):
        sent.append({"subject": subject, "body": body})

    from apps.nodes.models.node_core import NetMessage

    monkeypatch.setattr(NetMessage, "broadcast", classmethod(fake_broadcast))

    tasks._send_auto_upgrade_check_message("APPLIED-SUCCESSFULLY", "CLEAN")

    assert sent[0]["subject"] == "UP-CHECK 12:34"
    assert sent[0]["body"] == "APPLIED-SUCCESSF CLEAN"


def test_resolve_auto_upgrade_change_tag_for_version_change():
    assert (
        tasks._resolve_auto_upgrade_change_tag("1.0", "2.0", "aaa", "aaa")
        == "2.0"
    )


def test_resolve_auto_upgrade_change_tag_for_revision_change():
    assert (
        tasks._resolve_auto_upgrade_change_tag("1.0", "1.0", "abc", "1234567")
        == "234567"
    )


def test_resolve_auto_upgrade_change_tag_for_no_change():
    assert (
        tasks._resolve_auto_upgrade_change_tag("1.0", "1.0", "aaa", "aaa")
        == "CLEAN"
    )


def test_resolve_auto_upgrade_change_tag_for_none_version():
    assert (
        tasks._resolve_auto_upgrade_change_tag("1.0", None, "aaa", "aaa")
        == "-"
    )
