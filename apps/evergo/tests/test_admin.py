"""Tests for Evergo admin changelist presentation helpers."""

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model

from apps.evergo.admin import EvergoCustomerAdmin
from apps.evergo.models import EvergoCustomer, EvergoOrder, EvergoUser


@pytest.mark.django_db
def test_evergo_customer_admin_list_display_matches_requested_column_order():
    """Customers changelist should show SO first and omit the status column."""
    model_admin = EvergoCustomerAdmin(EvergoCustomer, admin.site)

    assert model_admin.list_display == (
        "latest_so_link",
        "name",
        "address_display",
        "brand_display",
        "phone_number_display",
    )


@pytest.mark.django_db
def test_evergo_customer_admin_brand_display_uses_latest_order_site_name():
    """Brand column should prioritize the linked latest order site name."""
    User = get_user_model()
    suite_user = User.objects.create_user(username="brand-owner", email="brand-owner@example.com")
    profile = EvergoUser.objects.create(user=suite_user, evergo_email="brand-owner@example.com")
    order = EvergoOrder.objects.create(user=profile, site_name="Constellation")
    customer = EvergoCustomer.objects.create(
        user=profile,
        name="Acme",
        latest_order=order,
        raw_payload={"orden_instalacion": {"marca_cargador": "Fallback"}},
    )

    model_admin = EvergoCustomerAdmin(EvergoCustomer, admin.site)

    assert model_admin.brand_display(customer) == "Constellation"


@pytest.mark.django_db
def test_evergo_customer_admin_brand_display_falls_back_to_raw_payload():
    """Brand column should use payload charger brand when no order site is linked."""
    User = get_user_model()
    suite_user = User.objects.create_user(username="brand-fallback", email="brand-fallback@example.com")
    profile = EvergoUser.objects.create(user=suite_user, evergo_email="brand-fallback@example.com")
    customer = EvergoCustomer.objects.create(
        user=profile,
        name="Acme",
        raw_payload={"orden_instalacion": {"marca_cargador": "Wallbox"}},
    )

    model_admin = EvergoCustomerAdmin(EvergoCustomer, admin.site)

    assert model_admin.brand_display(customer) == "Wallbox"
