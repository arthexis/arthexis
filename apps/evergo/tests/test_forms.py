"""Tests for Evergo admin form defaults and ownership behavior."""

import pytest
from django.contrib.auth import get_user_model

from apps.evergo.forms import EvergoContractorLoginWizardForm, EvergoLoadCustomersForm, EvergoUserAdminForm
from apps.evergo.models import EvergoUser


def test_load_customers_form_defaults_next_view_to_customers():
    """Customer ingest flow should default to opening the customers changelist."""
    form = EvergoLoadCustomersForm()

    assert form.fields["next_view"].initial == "customers"


@pytest.mark.django_db
def test_contractor_login_form_assigns_request_user_when_owner_fields_omitted():
    """Contractor wizard should auto-assign the authenticated actor as owner."""
    User = get_user_model()
    actor = User.objects.create_user(username="suite-owner", email="suite-owner@example.com")

    form = EvergoContractorLoginWizardForm(
        data={
            "evergo_email": "contractor@example.com",
            "evergo_password": "secret-pass",
            "validate_credentials": False,
            "load_all_customers": False,
        },
        request_user=actor,
    )

    assert form.is_valid(), form.errors
    assert form.cleaned_data["user"] == actor


@pytest.mark.django_db
def test_contractor_login_form_does_not_reassign_owner_on_existing_instance():
    """Existing contractors should still fail validation when all owner fields are cleared."""
    User = get_user_model()
    actor = User.objects.create_user(username="suite-editor", email="suite-editor@example.com")
    existing_owner = User.objects.create_user(username="existing-owner", email="existing-owner@example.com")

    contractor = EvergoUser.objects.create(
        user=existing_owner,
        evergo_email="existing@example.com",
        evergo_password="secret-pass",
    )

    form = EvergoContractorLoginWizardForm(
        data={
            "user": "",
            "group": "",
            "avatar": "",
            "evergo_email": "existing@example.com",
            "evergo_password": "",
            "validate_credentials": False,
            "load_all_customers": False,
        },
        instance=contractor,
        request_user=actor,
    )

    assert not form.is_valid()
    assert "__all__" in form.errors


@pytest.mark.django_db
def test_evergo_user_admin_form_assigns_request_user_on_create():
    """Evergo admin form should default empty ownership to the acting user on create."""
    User = get_user_model()
    actor = User.objects.create_user(username="owner-default", email="owner-default@example.com")

    form = EvergoUserAdminForm(
        data={
            "evergo_email": "new@example.com",
            "evergo_password": "secret-pass",
            "user": "",
            "group": "",
            "avatar": "",
        },
        request_user=actor,
    )

    assert form.is_valid(), form.errors
    assert form.cleaned_data["user"] == actor
