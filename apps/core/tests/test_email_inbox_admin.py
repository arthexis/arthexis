from __future__ import annotations

from django.contrib.auth.models import Permission
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
            "additional_inboxes": [],
            "test_now": "on",
        },
        follow=True,
    )

    assert response.status_code == 200
    messages = list(response.context["messages"])
    assert any("Mailbox unavailable" in str(message) for message in messages)


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
def test_setup_collector_view_renders_read_only_for_view_only_staff(client, admin_user):
    """Staff users with only inbox view permission should get a read-only setup preview."""

    inbox = EmailInbox.objects.create(
        user=admin_user,
        username="view-only-inbox@example.com",
        host="imap.example.com",
        port=993,
        password="secret",
    )
    collector = EmailCollector.objects.create(inbox=inbox, name="Existing")
    viewer = User.objects.create_user(username="staff-view-only", is_staff=True)
    viewer.user_permissions.add(Permission.objects.get(codename="view_emailinbox"))
    client.force_login(viewer)

    response = client.get(
        reverse("admin:emails_emailinbox_setup_collector", args=[inbox.pk])
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert 'value="Existing"' in content
    assert 'value="Save and run test"' not in content
    assert "not permission to change it" in content
    assert 'name="name"' in content
    assert 'disabled' in content


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
