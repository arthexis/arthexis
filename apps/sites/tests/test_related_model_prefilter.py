import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.urls import reverse

from apps.sites.templatetags.admin_extras import related_admin_models

pytestmark = pytest.mark.django_db


def test_related_admin_models_exposes_target_filter_lookups():
    """Related-model metadata should include lookup keys for selected-id prefiltering."""

    related = related_admin_models(get_user_model()._meta)

    groups_link = next(item for item in related if item["label"] == "Groups")

    assert "user__id__in" in groups_link["filter_lookups"]
    assert groups_link["source_model_label"] == "auth.user"


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

    selected_group = Group.objects.create(name="Selected Group")
    other_group = Group.objects.create(name="Other Group")

    selected_group.user_set.add(user_a)
    other_group.user_set.add(user_b)

    response = client.get(
        reverse("admin:auth_group_changelist"),
        {
            "__selected_ids": str(user_a.pk),
            "__relation_lookups": "user__id__in",
            "__source_model": "auth.user",
        },
    )

    assert response.status_code == 200
    changelist = response.context["cl"]
    result_pks = {group.pk for group in changelist.result_list}
    assert selected_group.pk in result_pks
    assert other_group.pk not in result_pks
