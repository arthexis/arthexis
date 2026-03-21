from __future__ import annotations

from django.contrib import admin
from django.urls import reverse

import pytest

from apps.evergo.exceptions import EvergoAPIError
from apps.evergo.models import EvergoUser


@pytest.mark.django_db
@pytest.mark.integration
def test_evergo_contractors_changelist_exposes_login_on_evergo_action(admin_client):
    response = admin_client.get(reverse("admin:evergo_evergouser_changelist"))

    assert response.status_code == 200
    content = response.content.decode()
    admin_instance = admin.site._registry[EvergoUser]
    assert admin_instance.get_changelist_actions(response.wsgi_request) == ("my_profile",)
    assert response.resolver_match.view_name == "admin:evergo_evergouser_changelist"
    assert "Login on Evergo" in content
    assert reverse("admin:evergo_evergouser_login_on_evergo") in content
    wizard_response = admin_client.get(reverse("admin:evergo_evergouser_login_on_evergo"))
    assert wizard_response.status_code == 200
    assert "Login on Evergo" in wizard_response.content.decode()


@pytest.mark.django_db
@pytest.mark.integration
def test_evergo_login_wizard_creates_contractor_validates_and_loads(admin_client, admin_user, monkeypatch):
    recorded_calls: list[tuple[str, int | str]] = []

    def fake_test_login(self, *, timeout: int = 15):
        recorded_calls.append(("login", timeout))
        self.evergo_user_id = 8801
        self.name = "Evergo Wizard"
        self.email = "wizard.contractor@example.com"
        self.save(update_fields=["evergo_user_id", "name", "email", "updated_at"])

        class _Result:
            response_code = 200

        return _Result()

    def fake_load_customers(self, *, raw_queries: str, timeout: int = 20):
        recorded_calls.append((raw_queries, timeout))
        return {
            "customers_loaded": 4,
            "orders_created": 2,
            "orders_updated": 1,
            "placeholders_created": 0,
            "unresolved": [],
            "loaded_customer_ids": [11],
            "loaded_order_ids": [22],
        }

    monkeypatch.setattr(EvergoUser, "test_login", fake_test_login)
    monkeypatch.setattr(EvergoUser, "load_customers_from_queries", fake_load_customers)

    response = admin_client.post(
        reverse("admin:evergo_evergouser_login_on_evergo"),
        {
            "user": str(admin_user.pk),
            "group": "",
            "avatar": "",
            "evergo_email": "wizard.contractor@example.com",
            "evergo_password": "top-secret",
            "validate_credentials": "on",
            "load_all_customers": "on",
        },
    )

    assert response.status_code == 200
    contractor = EvergoUser.objects.get(user=admin_user)
    assert contractor.evergo_email == "wizard.contractor@example.com"
    assert contractor.evergo_user_id == 8801
    assert recorded_calls == [("login", 15), ("", 20)]
    assert "Initial load completed." in response.content.decode()


@pytest.mark.django_db
@pytest.mark.integration
def test_evergo_login_wizard_does_not_create_contractor_when_validation_fails(
    admin_client, admin_user, monkeypatch
):
    def fake_test_login(self, *, timeout: int = 15):
        raise EvergoAPIError("bad credentials")

    monkeypatch.setattr(EvergoUser, "test_login", fake_test_login)

    response = admin_client.post(
        reverse("admin:evergo_evergouser_login_on_evergo"),
        {
            "user": str(admin_user.pk),
            "group": "",
            "avatar": "",
            "evergo_email": "broken.contractor@example.com",
            "evergo_password": "wrong-secret",
            "validate_credentials": "on",
            "_save": "Save and return to contractors",
        },
    )

    assert response.status_code == 200
    assert not EvergoUser.objects.filter(
        user=admin_user,
        evergo_email="broken.contractor@example.com",
    ).exists()
    assert "bad credentials" in response.content.decode()


@pytest.mark.django_db
@pytest.mark.integration
def test_evergo_user_admin_exposes_dashboard_action_and_object_wizard_redirect(admin_client, admin_user):
    contractor = EvergoUser.objects.create(
        user=admin_user,
        evergo_email="existing.contractor@example.com",
        evergo_password="secret",
    )
    admin_instance = admin.site._registry[EvergoUser]

    assert admin_instance.get_dashboard_actions(None) == ("login_on_evergo_dashboard_action",)

    response = admin_client.get(
        reverse("admin:evergo_evergouser_login_on_evergo_object", args=[contractor.pk])
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "existing.contractor@example.com" in content
    assert reverse("admin:evergo_evergouser_changelist") in content


@pytest.mark.django_db
@pytest.mark.integration
def test_evergo_login_wizard_keeps_existing_credentials_when_validation_fails(
    admin_client, admin_user, monkeypatch
):
    contractor = EvergoUser.objects.create(
        user=admin_user,
        evergo_email="existing.contractor@example.com",
        evergo_password="working-secret",
    )

    def fake_test_login(self, *, timeout: int = 15):
        raise EvergoAPIError("bad credentials")

    monkeypatch.setattr(EvergoUser, "test_login", fake_test_login)

    response = admin_client.post(
        reverse("admin:evergo_evergouser_login_on_evergo_object", args=[contractor.pk]),
        {
            "user": str(admin_user.pk),
            "group": "",
            "avatar": "",
            "evergo_email": "broken.contractor@example.com",
            "evergo_password": "",
            "validate_credentials": "on",
            "_save": "Save and return to contractors",
        },
    )

    assert response.status_code == 200
    contractor.refresh_from_db()
    assert contractor.evergo_email == "existing.contractor@example.com"
    assert contractor.evergo_password == "working-secret"
    assert "bad credentials" in response.content.decode()
