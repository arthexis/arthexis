"""Tests for the user-facing email inbox pages."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.emails.models import EmailInbox


pytestmark = pytest.mark.django_db()


@pytest.fixture()
def inbox_owner(db):
    """Create a reusable inbox owner for public inbox view tests."""

    return get_user_model().objects.create_user(
        username="inbox-owner",
        password="secret123",
        email="inbox-owner@example.com",
    )


@pytest.fixture()
def other_user(db):
    """Create another user to validate inbox ownership boundaries."""

    return get_user_model().objects.create_user(
        username="other-inbox-owner",
        password="secret123",
        email="other-inbox-owner@example.com",
    )


def _create_inbox(*, user, username: str, priority: int = 0) -> EmailInbox:
    """Create a minimal enabled inbox record for the given user."""

    return EmailInbox.objects.create(
        user=user,
        username=username,
        host="imap.example.com",
        port=993,
        password="secret",
        protocol=EmailInbox.IMAP,
        priority=priority,
        is_enabled=True,
    )


def test_inbox_list_requires_login(client):
    """Anonymous users should be redirected to the login page."""

    response = client.get(reverse("emails:inbox-list"))

    assert response.status_code == 302
    assert reverse("pages:login") in response.url


@pytest.mark.django_db()
def test_inbox_list_shows_recent_messages_for_selected_user_inbox(client, inbox_owner, monkeypatch):
    """Inbox list should show message rows for the signed-in user's inbox."""

    inbox = _create_inbox(user=inbox_owner, username="owner@example.com", priority=10)

    def fake_search_messages(self, limit=10, **kwargs):
        assert self.pk == inbox.pk
        assert limit == 100
        return [
            {
                "subject": "Quarterly update",
                "from": "ceo@example.com",
                "body": "Body one",
                "date": "Fri, 21 Mar 2026 10:30:00 +0000",
            },
            {
                "subject": "Welcome",
                "from": "support@example.com",
                "body": "Body two",
                "date": "Fri, 20 Mar 2026 11:00:00 +0000",
            },
        ]

    monkeypatch.setattr(EmailInbox, "search_messages", fake_search_messages)
    client.force_login(inbox_owner)

    response = client.get(reverse("emails:inbox-list"))

    assert response.status_code == 200
    assert response.context["selected_inbox"].pk == inbox.pk
    assert len(response.context["messages"]) == 2
    content = response.content.decode()
    assert "Quarterly update" in content
    assert "ceo@example.com" in content
    assert reverse("emails:inbox-detail", args=[0]) in content


@pytest.mark.django_db()
def test_inbox_detail_supports_navigation_between_messages(client, inbox_owner, monkeypatch):
    """Inbox detail should render the chosen message and expose previous/next navigation."""

    inbox = _create_inbox(user=inbox_owner, username="owner@example.com", priority=10)

    monkeypatch.setattr(
        EmailInbox,
        "search_messages",
        lambda self, limit=10, **kwargs: [
            {
                "subject": "First",
                "from": "first@example.com",
                "body": "First body",
                "date": "Fri, 21 Mar 2026 10:30:00 +0000",
            },
            {
                "subject": "Second",
                "from": "second@example.com",
                "body": "Second body",
                "date": "Fri, 21 Mar 2026 11:30:00 +0000",
            },
            {
                "subject": "Third",
                "from": "third@example.com",
                "body": "Third body",
                "date": "Fri, 21 Mar 2026 12:30:00 +0000",
            },
        ],
    )
    client.force_login(inbox_owner)

    response = client.get(reverse("emails:inbox-detail", args=[1]), {"inbox": inbox.pk})

    assert response.status_code == 200
    assert response.context["message"]["subject"] == "Second"
    assert response.context["navigation"] == {"previous_index": 0, "next_index": 2}
    content = response.content.decode()
    assert "Second body" in content
    assert reverse("emails:inbox-detail", args=[0]) in content
    assert reverse("emails:inbox-detail", args=[2]) in content


@pytest.mark.django_db()
def test_inbox_selection_rejects_other_users_inbox_ids(client, inbox_owner, other_user):
    """Users must not be able to select inbox ids owned by somebody else."""

    _create_inbox(user=inbox_owner, username="owner@example.com", priority=10)
    other_inbox = _create_inbox(user=other_user, username="other@example.com", priority=5)
    client.force_login(inbox_owner)

    response = client.get(reverse("emails:inbox-list"), {"inbox": other_inbox.pk})

    assert response.status_code == 403
