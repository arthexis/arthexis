"""Regression tests for ExperienceReference admin frame actions and viewers."""

import pytest
from django.urls import reverse

from apps.links.models.reference import ExperienceReference


@pytest.mark.django_db
def test_reference_changelist_shows_admin_and_public_frame_links(admin_client):
    """Ensure changelist renders links to admin and public frame viewers."""

    reference = ExperienceReference.objects.create(
        alt_text="SQLite",
        value="https://www.sqlite.org/",
    )

    response = admin_client.get(reverse("admin:links_experiencereference_changelist"))

    assert response.status_code == 200
    body = response.content.decode()
    assert reverse("admin:links_reference_frame", args=[reference.pk]) in body
    assert reverse("links:reference-public-frame", args=[reference.pk]) in body
    assert "Open in Frame" in body


@pytest.mark.django_db
def test_reference_admin_frame_view_renders_url_bar_without_label(admin_client):
    """Ensure admin frame view renders URL bar and omits the previous label text."""

    reference = ExperienceReference.objects.create(
        alt_text="SQLite",
        value="https://www.sqlite.org/",
    )

    response = admin_client.get(
        reverse("admin:links_reference_frame", args=[reference.pk])
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert 'id="frame-url-display"' in body
    assert 'id="reference-frame"' in body
    assert "Visited URL" not in body


@pytest.mark.django_db
def test_reference_public_frame_view_is_available_outside_admin(client):
    """Ensure the public frame view is served from site URLs, not admin URLs."""

    reference = ExperienceReference.objects.create(
        alt_text="SQLite",
        value="https://www.sqlite.org/",
    )

    response = client.get(reverse("links:reference-public-frame", args=[reference.pk]))

    assert response.status_code == 200
    body = response.content.decode()
    assert 'id="public-frame-url-display"' in body
    assert 'id="public-reference-frame"' in body
    assert "Visited URL" not in body


@pytest.mark.django_db
def test_reference_public_frame_view_hides_private_reference_for_anonymous(client):
    """Ensure anonymous users cannot access private references in public frame view."""

    reference = ExperienceReference.objects.create(
        alt_text="Private SQLite",
        value="https://www.sqlite.org/",
        include_in_footer=True,
        footer_visibility=ExperienceReference.FOOTER_PRIVATE,
    )

    response = client.get(reverse("links:reference-public-frame", args=[reference.pk]))

    assert response.status_code == 404


@pytest.mark.django_db
def test_reference_public_frame_view_allows_private_reference_for_authenticated_user(
    client, django_user_model
):
    """Ensure authenticated users can access private references in public frame view."""

    reference = ExperienceReference.objects.create(
        alt_text="Private SQLite",
        value="https://www.sqlite.org/",
        include_in_footer=True,
        footer_visibility=ExperienceReference.FOOTER_PRIVATE,
    )
    user = django_user_model.objects.create_user(username="viewer", password="pass1234")
    client.force_login(user)

    response = client.get(reverse("links:reference-public-frame", args=[reference.pk]))

    assert response.status_code == 200


@pytest.mark.django_db
def test_reference_public_frame_view_hides_staff_reference_for_non_staff(
    client, django_user_model
):
    """Ensure non-staff users cannot access staff-only references in public frame view."""

    reference = ExperienceReference.objects.create(
        alt_text="Staff SQLite",
        value="https://www.sqlite.org/",
        include_in_footer=True,
        footer_visibility=ExperienceReference.FOOTER_STAFF,
    )
    user = django_user_model.objects.create_user(username="member", password="pass1234")
    client.force_login(user)

    response = client.get(reverse("links:reference-public-frame", args=[reference.pk]))

    assert response.status_code == 404
