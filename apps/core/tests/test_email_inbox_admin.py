from __future__ import annotations

from django.contrib.auth.models import Permission
from django.urls import reverse

import pytest

from apps.emails.models import EmailCollector, EmailInbox
from apps.users.models import User

@pytest.mark.integration
@pytest.mark.django_db
def test_setup_collector_view_saves_collector_and_runs_preview(admin_client, admin_user, monkeypatch):
    """The setup wizard updates collector data and renders test results."""

    inbox = EmailInbox.objects.create(
        user=admin_user,
        username="wizard-inbox@example.com",
        host="imap.example.com",
        port=993,
        password="secret",
    )
    additional = EmailInbox.objects.create(
        user=User.objects.create_user(username="wizard-extra"),
        username="wizard-extra@example.com",
        host="imap.example.com",
        port=993,
        password="secret",
    )
    collector = EmailCollector.objects.create(inbox=inbox, name="Existing")

    def fake_search(self, limit=10):
        return [{"subject": "Match", "from": "sender@example.com", "body": "payload"}]

    monkeypatch.setattr(EmailCollector, "search_messages", fake_search)

    response = admin_client.post(
        reverse("admin:emails_emailinbox_setup_collector", args=[inbox.pk]),
        {
            "name": "Updated Collector",
            "subject": "invoice",
            "sender": "sender@example.com",
            "body": "",
            "fragment": "",
            "use_regular_expressions": "",
            "notification_mode": EmailCollector.NOTIFY_NONE,
            "notification_subject": "",
            "notification_message": "",
            "notification_recipients": "",
            "additional_inboxes": [str(additional.pk)],
            "test_now": "on",
        },
    )

    assert response.status_code == 200
    collector.refresh_from_db()
    assert collector.name == "Updated Collector"
    assert collector.additional_inboxes.filter(pk=additional.pk).exists()
    assert "Match" in response.rendered_content

@pytest.mark.integration
@pytest.mark.django_db
def test_setup_collector_view_forbids_staff_without_view_permission(client, admin_user):
    """Staff users without inbox view permission cannot open setup wizard."""

    inbox = EmailInbox.objects.create(
        user=admin_user,
        username="secured-inbox@example.com",
        host="imap.example.com",
        port=993,
        password="secret",
    )
    staff_user = User.objects.create_user(username="staff-no-view", is_staff=True)
    client.force_login(staff_user)

    response = client.get(
        reverse("admin:emails_emailinbox_setup_collector", args=[inbox.pk])
    )

    assert response.status_code == 403

