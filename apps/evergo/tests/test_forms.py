"""Tests for Evergo admin form defaults and ownership behavior."""

import pytest
from django.contrib.auth import get_user_model

from apps.evergo.forms import EvergoContractorLoginWizardForm, EvergoLoadCustomersForm


@pytest.mark.django_db
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
