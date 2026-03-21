"""Tests for EmailCollector notification routing."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from apps.core.models import EmailArtifact
from apps.emails.models import EmailCollector, EmailInbox


pytestmark = pytest.mark.django_db


def _create_owner(username: str):
    """Create and return a user for ownership checks."""

    return get_user_model().objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="password",
    )


def _create_inbox(owner, username: str) -> EmailInbox:
    """Create an inbox profile for collector tests."""

    return EmailInbox.objects.create(
        user=owner,
        username=username,
        host="imap.example.com",
        port=993,
        password="secret",
        protocol=EmailInbox.IMAP,
        use_ssl=True,
        is_enabled=True,
        priority=1,
    )


def test_collect_popup_notification_renders_sigil_templates(monkeypatch):
    """Collector popup notifications should resolve built-in and parsed sigils."""

    owner = _create_owner("collector-popup-owner")
    inbox = _create_inbox(owner, "collector-popup@example.com")
    collector = EmailCollector.objects.create(
        inbox=inbox,
        subject="Incident",
        fragment="Ticket [ticket_id] is [status]",
        notification_mode=EmailCollector.NOTIFY_POPUP,
        notification_subject="Alert [ticket_id] from [sender]",
        notification_message="Status [status]",
    )

    monkeypatch.setattr(
        inbox,
        "search_messages",
        lambda **kwargs: [
            {
                "subject": "Incident",
                "from": "alerts@example.com",
                "body": "Ticket 42 is open",
                "date": "Mon",
            }
        ],
    )
    captured: dict[str, str] = {}

    def _fake_notify(subject: str, body: str, **_kwargs) -> None:
        captured["subject"] = subject
        captured["body"] = body

    monkeypatch.setattr("apps.core.notifications.notify_async", _fake_notify)

    collector.collect(limit=1)

    artifact = EmailArtifact.objects.get(collector=collector)
    assert artifact.sigils == {"ticket_id": "42", "status": "open"}
    assert captured == {
        "subject": "Alert 42 from alerts@example.com",
        "body": "Status open",
    }


def test_collect_net_message_uses_broadcast(monkeypatch):
    """Collector net-message mode should publish the rendered notification."""

    owner = _create_owner("collector-net-owner")
    inbox = _create_inbox(owner, "collector-net@example.com")
    collector = EmailCollector.objects.create(
        inbox=inbox,
        notification_mode=EmailCollector.NOTIFY_NET_MESSAGE,
        notification_subject="[subject]",
        notification_message="from [sender]",
    )

    monkeypatch.setattr(
        inbox,
        "search_messages",
        lambda **kwargs: [
            {
                "subject": "Grid alarm",
                "from": "grid@example.com",
                "body": "Breaker open",
            }
        ],
    )
    captured: dict[str, str] = {}

    def _fake_broadcast(subject: str, body: str, **_kwargs):
        captured["subject"] = subject
        captured["body"] = body
        return object()

    monkeypatch.setattr("apps.nodes.models.NetMessage.broadcast", _fake_broadcast)

    collector.collect(limit=1)

    assert captured == {
        "subject": "Grid alarm",
        "body": "from grid@example.com",
    }


def test_collect_email_mode_sends_using_recipients(monkeypatch):
    """Collector email mode should send rendered content to configured recipients."""

    owner = _create_owner("collector-email-owner")
    inbox = _create_inbox(owner, "collector-email@example.com")
    collector = EmailCollector.objects.create(
        inbox=inbox,
        notification_mode=EmailCollector.NOTIFY_EMAIL,
        notification_subject="[subject]",
        notification_message="[body]",
        notification_recipients="ops@example.com, qa@example.com",
    )

    monkeypatch.setattr(
        inbox,
        "search_messages",
        lambda **kwargs: [
            {
                "subject": "Tariff update",
                "from": "market@example.com",
                "body": "Rate changed",
            }
        ],
    )
    captured: dict[str, object] = {}

    def _fake_send(subject, message, recipient_list, **kwargs):
        captured["subject"] = subject
        captured["message"] = message
        captured["recipient_list"] = recipient_list
        captured["kwargs"] = kwargs
        return 1

    monkeypatch.setattr("apps.emails.mailer.send", _fake_send)

    collector.collect(limit=1)

    assert captured["subject"] == "Tariff update"
    assert captured["message"] == "Rate changed"
    assert captured["recipient_list"] == ["ops@example.com", "qa@example.com"]
    assert captured["kwargs"]["fail_silently"] is False


def test_collect_email_mode_sanitizes_subject_newlines(monkeypatch):
    """Collector email mode should strip newlines from rendered subjects."""

    owner = _create_owner("collector-email-header-owner")
    inbox = _create_inbox(owner, "collector-email-header@example.com")
    collector = EmailCollector.objects.create(
        inbox=inbox,
        notification_mode=EmailCollector.NOTIFY_EMAIL,
        notification_subject="Alert [subject]",
        notification_message="[body]",
        notification_recipients="ops@example.com",
    )

    monkeypatch.setattr(
        inbox,
        "search_messages",
        lambda **kwargs: [
            {
                "subject": "Line1\nLine2\rLine3",
                "from": "market@example.com",
                "body": "Rate changed",
            }
        ],
    )

    captured: dict[str, object] = {}

    def _fake_send(subject, message, recipient_list, **kwargs):
        captured["subject"] = subject
        captured["message"] = message
        captured["recipient_list"] = recipient_list
        captured["kwargs"] = kwargs
        return 1

    monkeypatch.setattr("apps.emails.mailer.send", _fake_send)

    collector.collect(limit=1)

    assert captured["subject"] == "Alert Line1 Line2 Line3"


def test_collect_continues_when_notification_fails(monkeypatch, caplog):
    """Collector should keep processing later messages even if notification fails."""

    owner = _create_owner("collector-failure-owner")
    inbox = _create_inbox(owner, "collector-failure@example.com")
    collector = EmailCollector.objects.create(
        inbox=inbox,
        notification_mode=EmailCollector.NOTIFY_EMAIL,
        notification_subject="[subject]",
        notification_message="[body]",
        notification_recipients="ops@example.com",
    )

    monkeypatch.setattr(
        inbox,
        "search_messages",
        lambda **kwargs: [
            {"subject": "First", "from": "ops@example.com", "body": "One"},
            {"subject": "Second", "from": "ops@example.com", "body": "Two"},
        ],
    )

    def _fake_send(*_args, **_kwargs):
        raise RuntimeError("mail backend down")

    monkeypatch.setattr("apps.emails.mailer.send", _fake_send)

    with caplog.at_level("ERROR"):
        collector.collect(limit=2)

    assert EmailArtifact.objects.filter(collector=collector).count() == 2
    assert "Failed to send email notification for collector" in caplog.text


def test_collect_none_mode_skips_dispatch(monkeypatch):
    """Collector none mode should persist artifacts without dispatching notifications."""

    owner = _create_owner("collector-none-owner")
    inbox = _create_inbox(owner, "collector-none@example.com")
    collector = EmailCollector.objects.create(
        inbox=inbox,
        notification_mode=EmailCollector.NOTIFY_NONE,
    )

    monkeypatch.setattr(
        inbox,
        "search_messages",
        lambda **kwargs: [
            {
                "subject": "Maintenance",
                "from": "ops@example.com",
                "body": "Window starts",
            }
        ],
    )

    popup_calls = {"count": 0}

    def _fake_notify(*_args, **_kwargs) -> None:
        popup_calls["count"] += 1

    monkeypatch.setattr("apps.core.notifications.notify_async", _fake_notify)

    collector.collect(limit=1)

    assert EmailArtifact.objects.filter(collector=collector).count() == 1
    assert popup_calls["count"] == 0
