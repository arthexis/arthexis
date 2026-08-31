from __future__ import annotations

import pytest
from django.utils import timezone

from apps.emails.models import EmailCollector, EmailInbox
from apps.users.models import User


@pytest.mark.django_db
def test_collector_search_messages_reads_from_primary_and_additional_inboxes(monkeypatch):
    """Collectors aggregate matches from primary and additional inbox bindings."""

    owner = User.objects.create_user(username="collector-owner")
    primary = EmailInbox.objects.create(
        user=owner,
        username="primary@example.com",
        host="imap.example.com",
        port=993,
        password="secret",
    )
    secondary = EmailInbox.objects.create(
        user=User.objects.create_user(username="collector-secondary"),
        username="secondary@example.com",
        host="imap.example.com",
        port=993,
        password="secret",
    )

    collector = EmailCollector.objects.create(inbox=primary, subject="invoice")
    collector.additional_inboxes.add(secondary)

    def fake_search(self, **kwargs):
        if self.pk == primary.pk:
            return [{"subject": "invoice-a", "from": "first@example.com", "body": "A"}]
        return [{"subject": "invoice-b", "from": "second@example.com", "body": "B"}]

    monkeypatch.setattr(EmailInbox, "search_messages", fake_search)

    messages = collector.search_messages(limit=10)

    assert [item["subject"] for item in messages] == ["invoice-a", "invoice-b"]


@pytest.mark.django_db
def test_collector_collect_continues_to_additional_inbox_when_primary_has_known_message(monkeypatch):
    """Seen messages in primary inbox should not prevent polling secondary inboxes."""

    owner = User.objects.create_user(username="collector-owner-2")
    primary = EmailInbox.objects.create(
        user=owner,
        username="primary-2@example.com",
        host="imap.example.com",
        port=993,
        password="secret",
    )
    secondary = EmailInbox.objects.create(
        user=User.objects.create_user(username="collector-secondary-2"),
        username="secondary-2@example.com",
        host="imap.example.com",
        port=993,
        password="secret",
    )

    collector = EmailCollector.objects.create(inbox=primary, subject="invoice")
    collector.additional_inboxes.add(secondary)

    known = {
        "subject": "seen",
        "from": "known@example.com",
        "body": "known-body",
        "date": timezone.now(),
    }
    fresh = {
        "subject": "fresh",
        "from": "new@example.com",
        "body": "new-body",
        "date": timezone.now(),
    }

    def fake_search(self, **kwargs):
        if self.pk == primary.pk:
            return [known]
        return [fresh]

    monkeypatch.setattr(EmailInbox, "search_messages", fake_search)

    known_fp = collector.artifacts.model.fingerprint_for(
        known["subject"], known["from"], known["body"]
    )
    collector.artifacts.create(
        subject=known["subject"],
        sender=known["from"],
        body=known["body"],
        sigils={},
        fingerprint=known_fp,
    )

    collector.collect(limit=10)

    assert collector.artifacts.filter(subject="fresh", sender="new@example.com").exists()
