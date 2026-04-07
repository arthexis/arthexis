"""Tests for Evergo admin contractor login wizard behavior."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from apps.evergo.admin import _run_contract_login_validation
from apps.evergo.forms import EvergoContractorLoginWizardForm
from apps.evergo.models import EvergoUser


@pytest.mark.django_db
def test_contractor_login_wizard_form_forces_current_user_owner():
    """Wizard should always keep ownership on the current authenticated admin user."""
    User = get_user_model()
    acting_user = User.objects.create_user(username="acting", email="acting@example.com")
    other_user = User.objects.create_user(username="other", email="other@example.com")

    form = EvergoContractorLoginWizardForm(
        data={
            "user": other_user.pk,
            "evergo_email": "contractor@example.com",
            "evergo_password": "top-secret",  # noqa: S106
            "validate_credentials": "on",
            "order_numbers": "",
        },
        instance=EvergoUser(),
        request_user=acting_user,
    )

    assert form.is_valid(), form.errors
    assert form.cleaned_data["user"] == acting_user


@pytest.mark.django_db
def test_run_contract_login_validation_uses_order_numbers_when_full_load_disabled(monkeypatch):
    """Order-number input should trigger filtered loading when full customer load is disabled."""
    User = get_user_model()
    acting_user = User.objects.create_user(username="wizard", email="wizard@example.com")
    profile = EvergoUser.objects.create(
        user=acting_user,
        evergo_email="wizard@evergo.example",
        evergo_password="top-secret",  # noqa: S106
    )
    request = RequestFactory().post("/admin/evergo/evergouser/login-on-evergo/")
    request.user = acting_user
    messages: list[tuple[int, str]] = []
    admin_instance = SimpleNamespace(message_user=lambda _request, message, level: messages.append((level, str(message))))

    monkeypatch.setattr(
        profile,
        "test_login",
        lambda: SimpleNamespace(response_code=200),
    )
    captured_queries: list[str] = []

    def _fake_load_customers_from_queries(*, raw_queries: str):
        captured_queries.append(raw_queries)
        return {
            "customers_loaded": 1,
            "orders_created": 1,
            "orders_updated": 0,
            "placeholders_created": 0,
            "unresolved": [],
        }

    monkeypatch.setattr(profile, "load_customers_from_queries", _fake_load_customers_from_queries)
    form = SimpleNamespace(
        cleaned_data={
            "validate_credentials": True,
            "load_all_customers": False,
            "order_numbers": "J00123, J00456",
        }
    )

    result = _run_contract_login_validation(
        admin_instance,
        request,
        form,
        profile,
        show_setup_results=True,
    )

    assert result is not None
    assert captured_queries == ["J00123, J00456"]
