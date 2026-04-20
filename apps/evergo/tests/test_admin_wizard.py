"""Tests for Evergo admin contractor login wizard behavior."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from apps.chats.models import ChatAvatar
from apps.evergo.admin import (
    LOADED_ENTITIES_LINK_ID_LIMIT,
    _build_loaded_entities_links,
    _run_contract_login_validation,
)
from apps.evergo.forms import EvergoContractorLoginWizardForm
from apps.evergo.models import EvergoUser
from apps.groups.models import SecurityGroup


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
def test_contractor_login_wizard_form_clears_group_and_avatar_owners():
    """Existing group/avatar ownership should be cleared when wizard forces current user ownership."""
    User = get_user_model()
    acting_user = User.objects.create_user(username="acting", email="acting@example.com")
    owner_group = SecurityGroup.objects.create(name="Ops")
    owner_avatar = ChatAvatar.objects.create(name="Owner Avatar")
    profile = EvergoUser.objects.create(
        group=owner_group,
        avatar=owner_avatar,
        evergo_email="contractor@example.com",
        evergo_password="top-secret",  # noqa: S106
    )

    form = EvergoContractorLoginWizardForm(
        data={
            "evergo_email": "contractor@example.com",
            "evergo_password": "top-secret",  # noqa: S106
            "validate_credentials": "on",
            "order_numbers": "",
        },
        instance=profile,
        request_user=acting_user,
    )

    assert form.is_valid(), form.errors
    saved_profile = form.save()
    saved_profile.refresh_from_db()
    assert saved_profile.user == acting_user
    assert saved_profile.group is None
    assert saved_profile.avatar is None


@pytest.mark.django_db
def test_contractor_login_wizard_form_requires_validation_for_order_numbers():
    """Filtered order loads should require credential validation the same way full loads do."""
    User = get_user_model()
    acting_user = User.objects.create_user(username="acting", email="acting@example.com")

    form = EvergoContractorLoginWizardForm(
        data={
            "evergo_email": "contractor@example.com",
            "evergo_password": "top-secret",  # noqa: S106
            "validate_credentials": "",
            "order_numbers": "J00123, J00456",
        },
        instance=EvergoUser(),
        request_user=acting_user,
    )

    assert not form.is_valid()
    assert "order_numbers" in form.errors


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
            "loaded_customer_ids": [11],
            "loaded_order_ids": [22],
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
    assert len(messages) == 2
    assert "id__in=11" in messages[1][1]
    assert "id__in=22" in messages[1][1]
    assert result["admin_messages"][0]["status"] == "success"
    assert result["admin_messages"][1]["status"] == "success"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("payload", "expects_empty", "expected_parts", "unexpected_parts"),
    [
        (
            {"loaded_customer_ids": [11], "loaded_order_ids": []},
            False,
            ["id__in=11", "Customers"],
            ["Orders"],
        ),
        (
            {"loaded_customer_ids": [], "loaded_order_ids": []},
            True,
            [],
            [],
        ),
    ],
)
def test_build_loaded_entities_links_handles_presence_and_empty_states(
    payload, expects_empty, expected_parts, unexpected_parts
):
    """Link helper should include only loaded entities and emit empty output when nothing was loaded."""
    links = _build_loaded_entities_links(payload)
    if expects_empty:
        assert links == ""
        return
    for part in expected_parts:
        assert part in links
    for part in unexpected_parts:
        assert part not in links


@pytest.mark.django_db
def test_build_loaded_entities_links_limits_query_ids_to_prevent_overlong_urls():
    """Link helper should cap IDs so large imports do not emit overlong changelist URLs."""
    links = _build_loaded_entities_links(
        {
            "loaded_customer_ids": list(range(1, LOADED_ENTITIES_LINK_ID_LIMIT + 50)),
            "loaded_order_ids": [],
        }
    )

    assert f",{LOADED_ENTITIES_LINK_ID_LIMIT}" in links
    assert f",{LOADED_ENTITIES_LINK_ID_LIMIT + 1}" not in links
