"""Admin tests for Alexa account and reminder management."""

from __future__ import annotations

import pytest
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from apps.alexa.admin import AlexaAccountAdmin
from apps.alexa.models import AlexaAccount


@pytest.mark.django_db
def test_alexa_admin_test_credentials_updates_last_check_fields():
    """Regression: admin credential test action should store result metadata."""

    user = get_user_model().objects.create_user(
        username="alexa-admin-owner",
        is_staff=True,
        is_superuser=True,
    )
    valid = AlexaAccount.objects.create(
        name="Valid Alexa",
        user=user,
        client_id="valid-client",
        client_secret="valid-secret",
        refresh_token="valid-refresh",
    )
    invalid = AlexaAccount.objects.create(
        name="Invalid Alexa",
        user=user,
        client_id="",
        client_secret="secret",
        refresh_token="refresh",
    )

    model_admin = AlexaAccountAdmin(AlexaAccount, AdminSite())
    request = RequestFactory().post("/admin/alexa/alexaaccount/")
    request.user = user
    request.session = {}
    setattr(request, "_messages", FallbackStorage(request))

    model_admin.test_credentials(request, AlexaAccount.objects.filter(pk__in=[valid.pk, invalid.pk]))

    valid.refresh_from_db()
    invalid.refresh_from_db()
    assert valid.last_credentials_check_at is not None
    assert invalid.last_credentials_check_at is not None
    assert "look valid" in valid.last_credentials_check_message.lower()
    assert "missing" in invalid.last_credentials_check_message.lower()


@pytest.mark.django_db
def test_alexa_account_admin_places_owner_fields_in_last_section():
    """Regression: Alexa account admin should render ownable fields in the last fieldset."""

    user = get_user_model().objects.create_user(
        username="alexa-admin-layout-owner",
        is_staff=True,
        is_superuser=True,
    )
    model_admin = AlexaAccountAdmin(AlexaAccount, AdminSite())
    request = RequestFactory().get("/admin/alexa/alexaaccount/add/")
    request.user = user

    fieldsets = model_admin.get_fieldsets(request)

    assert fieldsets[-1][0] == "Owner"
    assert fieldsets[-1][1]["fields"] == ("user", "group")


@pytest.mark.django_db
def test_alexa_account_admin_masks_credential_fields_in_form():
    """Credential fields should be hidden until explicitly revealed in the UI."""

    user = get_user_model().objects.create_user(
        username="alexa-admin-masked-owner",
        is_staff=True,
        is_superuser=True,
    )
    model_admin = AlexaAccountAdmin(AlexaAccount, AdminSite())
    request = RequestFactory().get("/admin/alexa/alexaaccount/add/")
    request.user = user

    form_class = model_admin.get_form(request)
    form = form_class()

    for field_name in ("client_id", "client_secret", "refresh_token"):
        assert form.fields[field_name].widget.input_type == "password"
