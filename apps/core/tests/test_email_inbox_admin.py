from __future__ import annotations

from django.urls import reverse

import pytest

from apps.emails.models import EmailCollector, EmailInbox
from apps.users.models import User
@pytest.mark.integration
@pytest.mark.django_db
def test_setup_collector_tool_requires_single_selected_inbox(admin_client, admin_user):
    """The setup action should refuse requests that select more than one inbox."""

    inbox_a = EmailInbox.objects.create(
        user=admin_user,
        username="admin-a@example.com",
        host="imap.example.com",
        port=993,
        password="secret",
    )
    inbox_b = EmailInbox.objects.create(
        user=admin_user,
        username="admin-b@example.com",
        host="imap.example.com",
        port=993,
        password="secret",
    )

    response = admin_client.post(
        reverse("admin:emails_emailinbox_actions", kwargs={"tool": "setup_collector"}),
        {"_selected_action": [str(inbox_a.pk), str(inbox_b.pk)]},
        follow=True,
    )

    assert response.status_code == 200
    assert response.redirect_chain
    assert response.request["PATH_INFO"] == reverse("admin:emails_emailinbox_changelist")
    messages = [str(message) for message in response.context["messages"]]
    assert "Select exactly one inbox to start setup." in messages


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
            "notification_recipe": "",
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
def test_setup_collector_view_reports_non_validation_test_errors(admin_client, admin_user, monkeypatch):
    """Unexpected mailbox errors should be surfaced as admin feedback instead of a 500."""

    inbox = EmailInbox.objects.create(
        user=admin_user,
        username="wizard-error@example.com",
        host="imap.example.com",
        port=993,
        password="secret",
    )

    collector = EmailCollector.objects.create(inbox=inbox, name="Existing")

    def explode_search(self, limit=10):
        raise RuntimeError("Mailbox unavailable")

    monkeypatch.setattr(EmailCollector, "search_messages", explode_search)

    response = admin_client.post(
        reverse("admin:emails_emailinbox_setup_collector", args=[inbox.pk]),
        {
            "name": collector.name,
            "subject": "invoice",
            "sender": "sender@example.com",
            "body": "",
            "fragment": "",
            "use_regular_expressions": "",
            "notification_mode": EmailCollector.NOTIFY_NONE,
            "notification_subject": "",
            "notification_message": "",
            "notification_recipients": "",
            "notification_recipe": "",
            "additional_inboxes": [],
            "test_now": "on",
        },
        follow=True,
    )

    assert response.status_code == 200
    messages = [str(message) for message in response.context["messages"]]
    assert "Mailbox unavailable" in messages
