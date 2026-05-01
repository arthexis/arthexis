from __future__ import annotations

import pytest
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory
from django.urls import reverse

from apps.core.admin.emails import EmailCollectorAdmin
from apps.emails.models import EmailCollector, EmailInbox
from apps.odoo.models import OdooEmployee
from apps.users.models import User


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


@pytest.mark.django_db
def test_email_collector_admin_odoo_status_requires_all_customer_fields(admin_user):
    """The admin Odoo column is green only when all customer fields are present."""

    inbox = EmailInbox.objects.create(
        user=admin_user,
        username="odoo-admin-inbox@example.com",
        host="imap.example.com",
        port=993,
        password="secret",
    )
    profile = OdooEmployee.objects.create(
        user=admin_user,
        host="https://odoo.example.com",
        database="example",
        username="odoo@example.com",
        password="secret",
        odoo_uid=10,
    )
    collector = EmailCollector.objects.create(
        inbox=inbox,
        odoo_profile=profile,
        odoo_customer_name="Roberto Cuevas",
        odoo_customer_phone="+52 33 1234 5678",
        odoo_customer_address="Av Siempre Viva 123",
    )
    model_admin = EmailCollectorAdmin(EmailCollector, AdminSite())

    assert "#0a7f35" in str(model_admin.odoo_customer_status(collector))

    collector.odoo_customer_phone = ""
    assert "#b42318" in str(model_admin.odoo_customer_status(collector))


@pytest.mark.django_db
def test_email_collector_admin_limits_odoo_profiles_for_staff(admin_user):
    """Non-superusers can only select Odoo profiles they own."""

    staff_user = User.objects.create_user(
        username="odoo-profile-staff",
        is_staff=True,
    )
    owned_profile = OdooEmployee.objects.create(
        user=staff_user,
        host="https://odoo.example.com",
        database="example",
        username="staff-odoo@example.com",
        password="secret",
        odoo_uid=10,
    )
    other_profile = OdooEmployee.objects.create(
        user=admin_user,
        host="https://odoo.example.com",
        database="example",
        username="other-odoo@example.com",
        password="secret",
        odoo_uid=11,
    )
    request = RequestFactory().get("/")
    request.user = staff_user
    model_admin = EmailCollectorAdmin(EmailCollector, AdminSite())

    field = model_admin.formfield_for_foreignkey(
        EmailCollector._meta.get_field("odoo_profile"),
        request,
    )

    assert list(field.queryset) == [owned_profile]
    assert other_profile not in field.queryset
