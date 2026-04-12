from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from apps.odoo.admin import OdooQueryAdminForm
from apps.odoo.models import OdooQuery


@pytest.mark.django_db
def test_public_view_does_not_execute_for_anonymous_users(client, monkeypatch):
    query = OdooQuery.objects.create(
        name="Public Query",
        model_name="sale.order",
        method="search_read",
        enable_public_view=True,
        public_view_slug="public-query",
    )

    def _fail_execute(_self, _values=None):
        raise AssertionError("Public request should not execute query")

    monkeypatch.setattr(OdooQuery, "execute", _fail_execute)

    response = client.get(query.public_view_url(), {"partner": "acme"})

    assert response.status_code == 200
    assert response.context["ran_query"] is False
    assert "restricted to authenticated staff users" in response.context["error_message"]


@pytest.mark.django_db
def test_public_view_executes_for_staff_users(client, monkeypatch):
    query = OdooQuery.objects.create(
        name="Staff Query",
        model_name="sale.order",
        method="search_read",
        enable_public_view=True,
        public_view_slug="staff-query",
    )
    User = get_user_model()
    staff_user = User.objects.create_user(
        username="staff-user",
        password="testpass",
        is_staff=True,
    )

    def _execute(_self, _values=None):
        return [{"id": 7}]

    monkeypatch.setattr(OdooQuery, "execute", _execute)
    client.force_login(staff_user)

    response = client.get(query.public_view_url(), {"partner": "acme"})

    assert response.status_code == 200
    assert response.context["ran_query"] is True
    assert response.context["results"] == [{"id": 7}]


@pytest.mark.django_db
def test_model_validation_blocks_public_execution_without_secure_mode(monkeypatch):
    monkeypatch.setattr(
        "apps.odoo.models.query.is_public_query_execution_secure_mode_enabled",
        lambda default=False: False,
    )
    query = OdooQuery(
        name="Blocked Query",
        model_name="sale.order",
        method="search_read",
        enable_public_view=True,
    )

    with pytest.raises(ValidationError) as exc:
        query.full_clean()

    assert "enable_public_view" in exc.value.message_dict


@pytest.mark.django_db
def test_admin_form_shows_policy_error_when_secure_mode_disabled(monkeypatch):
    monkeypatch.setattr(
        "apps.odoo.admin.is_public_query_execution_secure_mode_enabled",
        lambda default=False: False,
    )

    form = OdooQueryAdminForm(
        data={
            "name": "Admin Blocked Query",
            "description": "",
            "profile": "",
            "model_name": "sale.order",
            "method": "search_read",
            "kwquery": "{}",
            "enable_public_view": "on",
            "public_title": "",
            "public_description": "",
        }
    )

    assert "blocked by policy" in form.fields["enable_public_view"].help_text
    assert form.is_valid() is False
    assert "secure-mode feature flag is disabled" in form.errors["enable_public_view"][0]
