from __future__ import annotations

from django.core.exceptions import ValidationError
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

import pytest

from apps.core.services.odoo_quote_report import (
    OdooQuoteReportData,
    OdooQuoteReportParams,
    build_odoo_quote_report_context_data,
)
from apps.odoo.models import OdooEmployee


@pytest.mark.django_db
def test_odoo_quote_report_rejects_invalid_query_params(admin_client, admin_user):
    """The report returns a 400 response when query parameters fail validation."""

    OdooEmployee.objects.create(
        user=admin_user,
        host="https://odoo.example.com",
        database="odoodb",
        username="admin",
        password="secret",
        odoo_uid=99,
        verified_on=timezone.now(),
    )

    response = admin_client.get(
        reverse("odoo-quote-report"),
        {"quote_window_days": "not-a-number"},
    )

    assert response.status_code == 400
    assert "Enter a whole number." in response.rendered_content


@pytest.mark.django_db
def test_odoo_quote_report_renders_empty_sections(admin_client, admin_user, monkeypatch):
    """The report renders empty-state rows when Odoo returns no matching records."""

    OdooEmployee.objects.create(
        user=admin_user,
        host="https://odoo.example.com",
        database="odoodb",
        username="admin",
        password="secret",
        odoo_uid=99,
        verified_on=timezone.now(),
    )

    def fake_execute(self, model, method, *args, **kwargs):
        return []

    monkeypatch.setattr(OdooEmployee, "execute", fake_execute)

    response = admin_client.get(reverse("odoo-quote-report"))

    assert response.status_code == 200
    assert "No templates found." in response.rendered_content
    assert "No matching quotes found." in response.rendered_content
    assert "No products found." in response.rendered_content
    assert "No installed modules found." in response.rendered_content


@pytest.mark.django_db
def test_odoo_quote_report_renders_successful_report(admin_client, admin_user, monkeypatch):
    """The report renders assembled quote, product, and module details from Odoo data."""

    OdooEmployee.objects.create(
        user=admin_user,
        host="https://odoo.example.com",
        database="odoodb",
        username="admin",
        password="secret",
        odoo_uid=99,
        verified_on=timezone.now(),
    )

    def fake_execute(self, model, method, *args, **kwargs):
        if model == "sale.order.template" and method == "search_read":
            return [{"id": 5, "name": "Starter Template"}]
        if model == "sale.order" and method == "read_group":
            return [
                {
                    "sale_order_template_id": [5, "Starter Template"],
                    "sale_order_template_id_count": 3,
                }
            ]
        if model == "sale.order" and method == "search_read":
            return [
                {
                    "name": "Q-0005",
                    "amount_total": 1250.5,
                    "partner_id": [9, "Acme Corp"],
                    "activity_type_id": [2, "Call"],
                    "activity_summary": "Follow up",
                    "tag_ids": [7],
                    "create_date": "2026-01-15 14:30:00",
                    "currency_id": [1, "USD"],
                }
            ]
        if model == "sale.order.tag" and method == "read":
            return [{"id": 7, "name": "Priority"}]
        if model == "res.currency" and method == "read":
            return [{"id": 1, "name": "USD", "symbol": "$"}]
        if model == "product.product" and method == "search_read":
            return [
                {
                    "name": "Fast Charger",
                    "default_code": "FC-01",
                    "create_date": "2026-01-10 08:00:00",
                    "write_date": "2026-01-16 09:15:00",
                }
            ]
        if model == "ir.module.module" and method == "search_read":
            return [
                {
                    "name": "sale_management",
                    "shortdesc": "Sales",
                    "latest_version": "1.0",
                    "author": "Odoo",
                }
            ]
        raise AssertionError(f"Unexpected call: {model}.{method}")

    monkeypatch.setattr(OdooEmployee, "execute", fake_execute)

    response = admin_client.get(
        reverse("odoo-quote-report"),
        {"quote_window_days": "30", "recent_product_limit": "5"},
    )

    assert response.status_code == 200
    assert "Starter Template" in response.rendered_content
    assert "Q-0005" in response.rendered_content
    assert "Acme Corp" in response.rendered_content
    assert "$1,250.50" in response.rendered_content
    assert "Priority" in response.rendered_content
    assert "Fast Charger" in response.rendered_content
    assert "sale_management" in response.rendered_content


def test_odoo_quote_report_params_use_defaults_for_blank_values():
    """Blank query parameters fall back to the default report settings."""

    request = RequestFactory().get(
        "/admin/core/odoo-quote-report/",
        {"quote_window_days": "", "recent_product_limit": ""},
    )

    params = OdooQuoteReportParams.from_request(request)

    assert params == OdooQuoteReportParams()


def test_odoo_quote_report_params_raise_validation_error_for_out_of_range_values():
    """Out-of-range query parameters produce field-specific validation errors."""

    request = RequestFactory().get(
        "/admin/core/odoo-quote-report/",
        {"quote_window_days": "0", "recent_product_limit": "101"},
    )

    with pytest.raises(ValidationError) as exc_info:
        OdooQuoteReportParams.from_request(request)

    assert "Ensure this value is between 1 and 365." in exc_info.value.messages


def test_build_odoo_quote_report_context_data_formats_presentation_values():
    """Presentation helpers format raw service data for the template context."""

    context = build_odoo_quote_report_context_data(
        OdooQuoteReportData(
            template_stats=[{"id": 1, "name": "Starter", "quote_count": 2}],
            quotes=[
                {
                    "name": "Q-1",
                    "partner_id": [3, "Example Customer"],
                    "activity_type_id": [4, "Email"],
                    "activity_summary": "",
                    "tag_names": ["Urgent"],
                    "create_date": "2026-01-15 14:30:00",
                    "amount_total": 450,
                    "currency": {"label": "€"},
                }
            ],
            recent_products=[
                {
                    "name": "Fast Charger",
                    "default_code": "FC-01",
                    "create_date": "2026-01-10 08:00:00",
                    "write_date": "2026-01-16 09:15:00",
                }
            ],
            installed_modules=[
                {
                    "name": "sale_management",
                    "shortdesc": "Sales",
                    "latest_version": "1.0",
                    "author": "Odoo",
                }
            ],
        )
    )

    quote = context["quotes"][0]
    product = context["recent_products"][0]

    assert quote["customer"] == "Example Customer"
    assert quote["activity"] == "Email"
    assert quote["total_display"] == "€450.00"
    assert quote["create_date"] is not None
    assert timezone.is_aware(quote["create_date"])
    assert product["write_date"] is not None
