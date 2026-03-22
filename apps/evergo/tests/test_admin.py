from __future__ import annotations

from django.contrib import admin
from django.urls import reverse

import pytest

from apps.evergo.models import EvergoCustomer, EvergoOrder, EvergoUser
from apps.evergo.exceptions import EvergoAPIError


@pytest.fixture
def evergo_customer_export_record(db):
    """Create a customer export fixture with full owner/profile graph."""

    user_model = get_user_model()

    def _create(*, username, email, remote_id, name):
        owner = user_model.objects.create_user(username=username, email=email)
        profile = EvergoUser.objects.create(
            user=owner,
            evergo_email=email,
            evergo_password="secret",  # noqa: S106
        )
        return EvergoCustomer.objects.create(
            user=profile,
            remote_id=remote_id,
            name=name,
            email=email,
        )

    return _create


@pytest.fixture
def evergo_order_for_admin_action(admin_client):
    """Create an EvergoOrder and its owner profile for admin action tests."""

    admin_user = admin_client.get(reverse("admin:index")).wsgi_request.user
    profile = EvergoUser.objects.create(
        user=admin_user,
        evergo_email="reload-action-test@evergo.example.com",
        evergo_password="secret",  # noqa: S106
    )
    return EvergoOrder.objects.create(user=profile, remote_id=8710, order_number="SO-8710")


@pytest.mark.django_db
def test_evergo_admin_load_customers_tool_action_is_registered_on_customers_only(admin_client):
    """Load-customers tool action should be exposed only for customers."""

    customer_tool_url = reverse("admin:evergo_evergocustomer_actions", args=["load_customers_wizard"])
    response = admin_client.get(customer_tool_url)

    assert response.status_code == 302
    assert response["Location"] == reverse("admin:evergo_evergocustomer_load_customers")

    contractor_tool_url = reverse("admin:evergo_evergouser_actions", args=["load_customers_wizard"])
    contractor_response = admin_client.get(contractor_tool_url)
    assert contractor_response.status_code == 404


@pytest.mark.django_db
def test_evergo_admin_load_orders_and_load_customers_actions_redirect_to_shared_wizard(admin_client):
    """Load orders and load customers actions should point to the same wizard."""

    wizard_url = reverse("admin:evergo_evergocustomer_load_customers")

    load_orders_action_url = reverse("admin:evergo_evergoorder_actions", args=["load_orders_wizard"])
    load_customers_action_url = reverse("admin:evergo_evergocustomer_actions", args=["load_customers_wizard"])

    load_orders_response = admin_client.get(load_orders_action_url)
    load_customers_response = admin_client.get(load_customers_action_url)


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
    content = response.content.decode()
    assert "bad credentials" in content
    assert '/change/' not in content


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
    assert admin_instance.login_on_evergo_dashboard_action.requires_queryset is False

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
    body = response.content.decode("utf-8")
    assert "7301\tHidden Selected" in body
    assert "7302\tHidden Unselected" not in body


@pytest.mark.django_db
def test_evergo_customer_admin_get_queryset_limits_non_superuser_visibility():
    """Non-superusers should only see customers for their own Evergo profile."""

    user_model = get_user_model()
    owner = user_model.objects.create_user(
        username="suite-admin-owner-visible",
        email="suite-admin-owner-visible@example.com",
        is_staff=True,
    )
    other_owner = user_model.objects.create_user(
        username="suite-admin-owner-hidden",
        email="suite-admin-owner-hidden@example.com",
        is_staff=True,
    )

    owner_profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="suite-admin-owner-visible@example.com",
        evergo_password="secret",  # noqa: S106
    )
    other_profile = EvergoUser.objects.create(
        user=other_owner,
        evergo_email="suite-admin-owner-hidden@example.com",
        evergo_password="secret",  # noqa: S106
    )

    visible_customer = EvergoCustomer.objects.create(user=owner_profile, name="Visible Customer")
    EvergoCustomer.objects.create(user=other_profile, name="Hidden Customer")

    model_admin = admin.site._registry[EvergoCustomer]
    request = RequestFactory().get(reverse("admin:evergo_evergocustomer_changelist"))
    request.user = owner

    queryset = model_admin.get_queryset(request)

    assert list(queryset.values_list("id", flat=True)) == [visible_customer.id]


@pytest.mark.django_db
def test_evergo_order_admin_get_queryset_limits_non_superuser_visibility():
    """Non-superusers should only see orders for their own Evergo profile."""

    user_model = get_user_model()
    owner = user_model.objects.create_user(
        username="suite-admin-order-owner-visible",
        email="suite-admin-order-owner-visible@example.com",
        is_staff=True,
    )
    other_owner = user_model.objects.create_user(
        username="suite-admin-order-owner-hidden",
        email="suite-admin-order-owner-hidden@example.com",
        is_staff=True,
    )

    owner_profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="suite-admin-order-owner-visible@example.com",
        evergo_password="secret",  # noqa: S106
    )
    other_profile = EvergoUser.objects.create(
        user=other_owner,
        evergo_email="suite-admin-order-owner-hidden@example.com",
        evergo_password="secret",  # noqa: S106
    )

    visible_order = EvergoOrder.objects.create(user=owner_profile, remote_id=9911, order_number="SO-9911")
    EvergoOrder.objects.create(user=other_profile, remote_id=9912, order_number="SO-9912")

    model_admin = admin.site._registry[EvergoOrder]
    request = RequestFactory().get(reverse("admin:evergo_evergoorder_changelist"))
    request.user = owner

    queryset = model_admin.get_queryset(request)

    assert list(queryset.values_list("id", flat=True)) == [visible_order.id]


@pytest.mark.django_db
def test_evergo_admin_load_customers_wizard_load_all_button_requires_confirmation(admin_client):
    """Load-all action should require explicit user confirmation in UI."""

    wizard_url = reverse("admin:evergo_evergocustomer_load_customers")
    response = admin_client.get(wizard_url)

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "Load all customers" in content
    assert "return confirm('This will sync every customer available to this profile. Continue?');" in content


@pytest.mark.django_db
def test_evergo_order_reload_change_action_rejects_get_requests(admin_client, evergo_order_for_admin_action):
    """Reload action should reject GET to avoid CSRF-prone state changes."""

    order = evergo_order_for_admin_action

    action_url = reverse(
        "admin:evergo_evergoorder_actions",
        args=[order.pk, "reload_from_evergo_action"],
    )
    response = admin_client.get(action_url)

    assert response.status_code == 405


@pytest.mark.django_db
@patch("apps.evergo.models.user.EvergoUser.reload_order_from_remote")
def test_evergo_order_reload_change_action_allows_post(
    mock_reload_order, admin_client, evergo_order_for_admin_action
):
    """Reload action should execute only on POST and redirect back to order change page."""

    order = evergo_order_for_admin_action
    mock_reload_order.return_value = order

    action_url = reverse(
        "admin:evergo_evergoorder_actions",
        args=[order.pk, "reload_from_evergo_action"],
    )
    response = admin_client.post(action_url)

    assert response.status_code == 302
    assert response["Location"] == reverse("admin:evergo_evergoorder_change", args=[order.pk])
    mock_reload_order.assert_called_once_with(order=order)
