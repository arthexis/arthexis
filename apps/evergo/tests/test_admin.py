"""Admin tests for the Evergo integration app."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.evergo.models import EvergoUser


@pytest.mark.django_db
def test_evergo_admin_app_and_changelist_are_accessible(admin_client):
    """Ensure Evergo appears in admin and the model changelist renders."""

    app_url = reverse("admin:app_list", kwargs={"app_label": "evergo"})
    changelist_url = reverse("admin:evergo_evergouser_changelist")

    app_response = admin_client.get(app_url)
    changelist_response = admin_client.get(changelist_url)

    assert app_response.status_code == 200
    assert changelist_response.status_code == 200


@pytest.mark.django_db
def test_evergo_admin_change_form_renders_readonly_synced_fields(admin_client):
    """Ensure synced Evergo fields render in change form for profile inspection."""

    user_model = get_user_model()
    suite_user = user_model.objects.create_user(
        username="suite-admin-preview",
        email="suite-admin-preview@example.com",
    )
    profile = EvergoUser.objects.create(
        user=suite_user,
        evergo_email="suite-admin-preview@example.com",
        evergo_password="secret",  # noqa: S106
        evergo_user_id=123,
        name="Suite Admin Preview",
        email="suite-admin-preview@example.com",
        empresa_id=99,
        subempresa_id=100,
    )

    change_url = reverse("admin:evergo_evergouser_change", args=[profile.pk])
    response = admin_client.get(change_url)

    assert response.status_code == 200
    assert b"Evergo synced profile" in response.content
    assert b"Two-factor" in response.content


@pytest.mark.django_db
def test_evergo_admin_changelist_shows_evergo_email_instead_of_internal_ids(admin_client):
    """Ensure changelist prioritizes Evergo email over internal identifier columns."""

    user_model = get_user_model()
    suite_user = user_model.objects.create_user(
        username="suite-admin-listing",
        email="suite-admin-listing@example.com",
    )
    EvergoUser.objects.create(
        user=suite_user,
        evergo_email="suite-listing@evergo.example.com",
        evergo_password="secret",  # noqa: S106
    )

    changelist_url = reverse("admin:evergo_evergouser_changelist")
    response = admin_client.get(changelist_url)

    assert response.status_code == 200

    content = response.content.lower()
    assert b"suite-listing@evergo.example.com" in content

    table_start = content.find(b"<table id=\"result_list\"")
    assert table_start != -1

    table_content = content[table_start:]
    thead_start = table_content.find(b"<thead")
    thead_end = table_content.find(b"</thead>")
    assert thead_start != -1 and thead_end != -1

    thead = table_content[thead_start:thead_end]
    assert b"evergo email" in thead
    assert b"evergo user id" not in thead
    assert b">empresa id<" not in thead
    assert b">subempresa id<" not in thead
