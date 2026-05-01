"""Tests for EmailCollector notification routing."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from apps.core.models import EmailArtifact
from apps.emails.models import EmailCollector, EmailInbox
from apps.odoo.models import OdooEmployee

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


def _create_odoo_profile(owner, username: str = "odoo@example.com") -> OdooEmployee:
    """Create a verified-looking Odoo profile for collector validation tests."""

    return OdooEmployee.objects.create(
        user=owner,
        host="https://odoo.example.com",
        database="example",
        username=username,
        password="secret",
        odoo_uid=10,
    )


def test_collect_refreshes_complete_odoo_customer_snapshot(monkeypatch):
    """A new collected email should poll Odoo and mark complete customer data."""

    owner = _create_owner("collector-odoo-complete-owner")
    inbox = _create_inbox(owner, "collector-odoo-complete@example.com")
    profile = _create_odoo_profile(owner)
    collector = EmailCollector.objects.create(
        inbox=inbox,
        fragment="Cliente: [customer_name]",
        odoo_profile=profile,
        odoo_customer_name_sigil="customer_name",
    )

    monkeypatch.setattr(
        inbox,
        "search_messages",
        lambda **kwargs: [
            {
                "subject": "Porsche quote",
                "from": "tecnologia@gelectriic.com",
                "body": "Cliente: Roberto Cuevas",
            }
        ],
    )
    calls: list[tuple[str, str, tuple, dict]] = []

    def _fake_execute(self, model, method, *args, **kwargs):
        calls.append((model, method, args, kwargs))
        return [
            {
                "name": "Roberto Cuevas",
                "phone": "",
                "mobile": "+52 33 1234 5678",
                "street": "Av Siempre Viva 123",
                "street2": "Interior 4",
                "zip": "44100",
                "city": "Guadalajara",
                "state_id": [14, "Jalisco"],
                "country_id": [156, "Mexico"],
            }
        ]

    monkeypatch.setattr(OdooEmployee, "execute", _fake_execute)

    collector.collect(limit=1)

    collector.refresh_from_db()
    assert collector.odoo_customer_name == "Roberto Cuevas"
    assert collector.odoo_customer_phone == "+52 33 1234 5678"
    assert (
        collector.odoo_customer_address
        == "Av Siempre Viva 123, Interior 4, 44100 Guadalajara, Jalisco, Mexico"
    )
    assert collector.odoo_customer_checked_at is not None
    assert collector.odoo_customer_fields_complete is True
    assert calls[0][0:2] == ("res.partner", "search_read")
    assert calls[0][2][0] == [[("name", "=", "Roberto Cuevas")]]
    assert calls[0][3]["limit"] == 2


def test_collect_marks_odoo_customer_snapshot_incomplete(monkeypatch):
    """Missing Odoo address or phone should keep the validation incomplete."""

    owner = _create_owner("collector-odoo-incomplete-owner")
    inbox = _create_inbox(owner, "collector-odoo-incomplete@example.com")
    profile = _create_odoo_profile(owner, username="odoo-incomplete@example.com")
    collector = EmailCollector.objects.create(
        inbox=inbox,
        fragment="Cliente: [customer_name]",
        odoo_profile=profile,
    )

    monkeypatch.setattr(
        inbox,
        "search_messages",
        lambda **kwargs: [
            {
                "subject": "Porsche quote",
                "from": "tecnologia@gelectriic.com",
                "body": "Cliente: Roberto Cuevas",
            }
        ],
    )

    def _fake_execute(self, model, method, *args, **kwargs):
        return [
            {
                "name": "Roberto Cuevas",
                "phone": "",
                "mobile": "",
                "street": "",
                "street2": "",
                "zip": "",
                "city": "",
                "state_id": False,
                "country_id": False,
            }
        ]

    monkeypatch.setattr(OdooEmployee, "execute", _fake_execute)

    collector.collect(limit=1)

    collector.refresh_from_db()
    assert collector.odoo_customer_name == "Roberto Cuevas"
    assert collector.odoo_customer_phone == ""
    assert collector.odoo_customer_address == ""
    assert collector.odoo_customer_checked_at is not None
    assert collector.odoo_customer_fields_complete is False


def test_collect_updates_odoo_snapshot_once_from_newest_created_email(monkeypatch):
    """Collector-level Odoo validation should run once per collection cycle."""

    owner = _create_owner("collector-odoo-once-owner")
    inbox = _create_inbox(owner, "collector-odoo-once@example.com")
    profile = _create_odoo_profile(owner, username="odoo-once@example.com")
    collector = EmailCollector.objects.create(
        inbox=inbox,
        fragment="Cliente: [customer_name]",
        odoo_profile=profile,
    )

    monkeypatch.setattr(
        inbox,
        "search_messages",
        lambda **kwargs: [
            {
                "subject": "Newest quote",
                "from": "tecnologia@gelectriic.com",
                "body": "Cliente: Roberto Cuevas",
            },
            {
                "subject": "Older quote",
                "from": "tecnologia@gelectriic.com",
                "body": "Cliente: Older Customer",
            },
        ],
    )
    calls: list[tuple[str, str, tuple, dict]] = []

    def _fake_execute(self, model, method, *args, **kwargs):
        calls.append((model, method, args, kwargs))
        return [
            {
                "name": "Roberto Cuevas",
                "phone": "+52 33 1234 5678",
                "mobile": "",
                "street": "Av Siempre Viva 123",
                "street2": "",
                "zip": "",
                "city": "Guadalajara",
                "state_id": False,
                "country_id": False,
            }
        ]

    monkeypatch.setattr(OdooEmployee, "execute", _fake_execute)

    collector.collect(limit=2)

    collector.refresh_from_db()
    assert EmailArtifact.objects.filter(collector=collector).count() == 2
    assert len(calls) == 1
    assert calls[0][2][0] == [[("name", "=", "Roberto Cuevas")]]
    assert collector.odoo_customer_name == "Roberto Cuevas"


def test_collect_rejects_partial_odoo_customer_name_match(monkeypatch):
    """Partial Odoo matches must not populate another customer's snapshot."""

    owner = _create_owner("collector-odoo-partial-owner")
    inbox = _create_inbox(owner, "collector-odoo-partial@example.com")
    profile = _create_odoo_profile(owner, username="odoo-partial@example.com")
    collector = EmailCollector.objects.create(
        inbox=inbox,
        fragment="Cliente: [customer_name]",
        odoo_profile=profile,
    )

    monkeypatch.setattr(
        inbox,
        "search_messages",
        lambda **kwargs: [
            {
                "subject": "Porsche quote",
                "from": "tecnologia@gelectriic.com",
                "body": "Cliente: Roberto Cuevas",
            }
        ],
    )

    def _fake_execute(self, model, method, *args, **kwargs):
        return [
            {
                "name": "Roberto Cuevas Logistics",
                "phone": "+52 33 9999 0000",
                "mobile": "",
                "street": "Wrong customer street",
                "street2": "",
                "zip": "",
                "city": "Guadalajara",
                "state_id": False,
                "country_id": False,
            }
        ]

    monkeypatch.setattr(OdooEmployee, "execute", _fake_execute)

    collector.collect(limit=1)

    collector.refresh_from_db()
    assert collector.odoo_customer_name == "Roberto Cuevas"
    assert collector.odoo_customer_phone == ""
    assert collector.odoo_customer_address == ""
    assert collector.odoo_customer_fields_complete is False
