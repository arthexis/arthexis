from __future__ import annotations

import pytest

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
