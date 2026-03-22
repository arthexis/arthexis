from __future__ import annotations

from django.urls import reverse

import pytest

from apps.emails.models import EmailCollector, EmailInbox
from apps.users.models import User


@pytest.mark.integration
@pytest.mark.django_db
def test_setup_collector_tool_redirects_to_wizard(admin_client, admin_user):
    """The inbox setup collector changelist tool redirects to the wizard endpoint."""

    inbox = EmailInbox.objects.create(
        user=admin_user,
        username="admin-inbox@example.com",
        host="imap.example.com",
        port=993,
        password="secret",
    )

    response = admin_client.post(
        reverse("admin:emails_emailinbox_actions", kwargs={"tool": "setup_collector"}),
        {"_selected_action": [str(inbox.pk)]},
    )

    assert response.status_code == 302
    assert response.url == reverse("admin:emails_emailinbox_setup_collector", args=[inbox.pk])


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
def test_setup_collector_tool_requires_single_selection(admin_client):
    """The setup collector tool rejects empty or multi-inbox selections."""

    response = admin_client.post(
        reverse("admin:emails_emailinbox_actions", kwargs={"tool": "setup_collector"}),
        {"_selected_action": []},
        follow=True,
    )

    assert response.status_code == 200
    messages = list(response.context["messages"])
    assert any("Select exactly one inbox to start setup." in str(message) for message in messages)


@pytest.mark.integration
@pytest.mark.django_db
def test_setup_collector_tool_rejects_multiple_selected_inboxes(admin_client, admin_user):
    """The setup collector tool does not pick an arbitrary inbox from multiple selections."""

    first = EmailInbox.objects.create(
        user=admin_user,
        username="first@example.com",
        host="imap.example.com",
        port=993,
        password="secret",
    )
    second = EmailInbox.objects.create(
        user=admin_user,
        username="second@example.com",
        host="imap.example.com",
        port=993,
        password="secret",
    )

    response = admin_client.post(
        reverse("admin:emails_emailinbox_actions", kwargs={"tool": "setup_collector"}),
        {"_selected_action": [str(first.pk), str(second.pk)]},
        follow=True,
    )

    assert response.status_code == 200
    messages = list(response.context["messages"])
    assert any("Select exactly one inbox to start setup." in str(message) for message in messages)


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


@pytest.mark.integration
@pytest.mark.django_db
def test_setup_collector_view_forbids_staff_without_change_permission(client, admin_user):
    """Staff users without inbox change permission cannot update collectors."""

    inbox = EmailInbox.objects.create(
        user=admin_user,
        username="secured-update@example.com",
        host="imap.example.com",
        port=993,
        password="secret",
    )
    staff_user = User.objects.create_user(username="staff-no-change", is_staff=True)
    client.force_login(staff_user)

    response = client.post(
        reverse("admin:emails_emailinbox_setup_collector", args=[inbox.pk]),
        {
            "name": "Blocked Collector",
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
    )

    assert response.status_code == 403
    assert not inbox.collectors.filter(name="Blocked Collector").exists()
