import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.emails.models.inbox import EmailInbox
from apps.sites.templatetags.admin_extras import related_admin_models

pytestmark = pytest.mark.django_db


def test_related_admin_models_exposes_target_filter_lookups():
    """Related-model metadata should include lookup keys for selected-id prefiltering."""

    related = related_admin_models(get_user_model()._meta)

    assert isinstance(related, list)
    assert related
    assert any(
        item.get("filter_lookups") and "selected-id" in item["filter_lookups"]
        for item in related
    )


def test_related_selection_prefilter_limits_target_admin_results(client):
    """Target changelist should show only records related to selected source rows."""

    admin_user = get_user_model().objects.create_superuser(
        username="admin-prefilter",
        password="admin123",
        email="admin-prefilter@example.com",
    )
    client.force_login(admin_user)

    user_a = get_user_model().objects.create_user(
        username="selected-user",
        email="selected-user@example.com",
        password="admin123",
    )
    user_b = get_user_model().objects.create_user(
        username="non-selected-user",
        email="non-selected-user@example.com",
        password="admin123",
    )

    selected_inbox = EmailInbox.objects.create(
        user=user_a,
        username="selected@example.com",
        host="imap.example.com",
        password="secret",
        port=993,
    )
    other_inbox = EmailInbox.objects.create(
        user=user_b,
        username="other@example.com",
        host="imap.example.com",
        password="secret",
        port=993,
    )

    response = client.get(
        reverse("admin:emails_emailinbox_changelist"),
        {
            "__selected_ids": str(user_a.pk),
            "__relation_lookups": "user__id__in",
            "__source_model": "auth.user",
        },
    )

    assert response.status_code == 200
    changelist = response.context["cl"]
    result_pks = {item.pk for item in changelist.result_list}
    assert selected_inbox.pk in result_pks
    assert other_inbox.pk not in result_pks
