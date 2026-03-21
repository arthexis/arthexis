"""Regression tests for admin-exposed public view shortcuts."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.urls import reverse

from apps.extensions.models import JsExtension
from apps.shop.models import Shop, ShopOrder
from apps.terms.models import Term

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_user():
    """Create a superuser that can inspect admin object tools."""

    return get_user_model().objects.create_superuser(
        username="admin-public-links",
        email="admin-public-links@example.com",
        password="admin123",
    )


def test_term_admin_shows_registration_and_detail_links_for_accessible_terms(client, admin_user):
    """Term admin pages should expose registration and accessible detail routes."""

    client.force_login(admin_user)
    term = Term.objects.create(
        title="Operations Policy",
        slug="operations-policy",
        category=Term.Category.GENERAL,
    )

    changelist_response = client.get(reverse("admin:terms_term_changelist"))
    assert changelist_response.status_code == 200
    assert reverse("terms:registration") in changelist_response.content.decode()

    change_response = client.get(reverse("admin:terms_term_change", args=[term.pk]))
    assert change_response.status_code == 200
    content = change_response.content.decode()
    assert reverse("terms:registration") in content
    assert term.get_absolute_url() in content


def test_term_admin_hides_protected_detail_links_for_non_members(client):
    """Protected term links should stay hidden for staff users outside the required group."""

    staff_user = get_user_model().objects.create_user(
        username="staff-public-links",
        email="staff-public-links@example.com",
        password="admin123",
        is_staff=True,
    )
    staff_user.user_permissions.add(
        Permission.objects.get(codename="view_term"),
        Permission.objects.get(codename="change_term"),
    )
    protected_group = Group.objects.create(name="Protected Terms")
    term = Term.objects.create(
        title="Protected Policy",
        slug="protected-policy",
        category=Term.Category.SECURITY_GROUP,
        security_group=protected_group,
    )

    client.force_login(staff_user)
    change_response = client.get(reverse("admin:terms_term_change", args=[term.pk]))
    assert change_response.status_code == 200
    content = change_response.content.decode()
    assert reverse("terms:registration") in content
    assert term.get_absolute_url() not in content


def test_shop_admin_pages_show_storefront_and_tracking_links(client, admin_user):
    """Shop admins should expose storefront and order tracking public routes."""

    client.force_login(admin_user)
    shop = Shop.objects.create(name="Campus Shop", slug="campus-shop")
    order = ShopOrder.objects.create(
        shop=shop,
        customer_name="Ada Lovelace",
        customer_email="ada@example.com",
        shipping_address_line1="1 Main Street",
        shipping_city="London",
        shipping_postal_code="EC1A 1AA",
        shipping_country="UK",
    )

    shop_change_response = client.get(reverse("admin:shop_shop_change", args=[shop.pk]))
    assert shop_change_response.status_code == 200
    assert reverse("shop:index") in shop_change_response.content.decode()

    order_change_response = client.get(
        reverse("admin:shop_shoporder_change", args=[order.pk])
    )
    assert order_change_response.status_code == 200
    content = order_change_response.content.decode()
    assert reverse("shop:index") in content
    assert (
        reverse("shop:order_tracking", kwargs={"tracking_token": order.tracking_token})
        in content
    )


def test_extension_admin_shows_catalog_and_asset_links_for_enabled_extensions(client, admin_user):
    """Enabled JS extension admin pages should expose catalog and served asset routes."""

    client.force_login(admin_user)
    extension = JsExtension.objects.create(
        slug="ops-helper",
        name="Ops Helper",
        content_script="console.log('ready')",
        background_script="console.log('background')",
        options_page="<html><body>Options</body></html>",
    )

    changelist_response = client.get(reverse("admin:extensions_jsextension_changelist"))
    assert changelist_response.status_code == 200
    assert reverse("extensions:catalog") in changelist_response.content.decode()

    change_response = client.get(
        reverse("admin:extensions_jsextension_change", args=[extension.pk])
    )
    assert change_response.status_code == 200
    content = change_response.content.decode()
    assert reverse("extensions:catalog") in content
    assert reverse("extensions:manifest", args=[extension.slug]) in content
    assert reverse("extensions:download", args=[extension.slug]) in content
    assert reverse("extensions:content", args=[extension.slug]) in content
    assert reverse("extensions:background", args=[extension.slug]) in content
    assert reverse("extensions:options", args=[extension.slug]) in content


def test_extension_admin_hides_asset_links_for_disabled_extensions(client, admin_user):
    """Disabled JS extension admin pages should not advertise dead asset routes."""

    client.force_login(admin_user)
    extension = JsExtension.objects.create(
        slug="disabled-helper",
        name="Disabled Helper",
        is_enabled=False,
        content_script="console.log('ready')",
        background_script="console.log('background')",
        options_page="<html><body>Options</body></html>",
    )

    change_response = client.get(
        reverse("admin:extensions_jsextension_change", args=[extension.pk])
    )
    assert change_response.status_code == 200
    content = change_response.content.decode()
    assert reverse("extensions:catalog") in content
    assert reverse("extensions:manifest", args=[extension.slug]) not in content
    assert reverse("extensions:download", args=[extension.slug]) not in content
    assert reverse("extensions:content", args=[extension.slug]) not in content
    assert reverse("extensions:background", args=[extension.slug]) not in content
    assert reverse("extensions:options", args=[extension.slug]) not in content
