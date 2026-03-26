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
@pytest.mark.django_db
def test_odoo_quote_report_params_raise_validation_error_for_out_of_range_values():
    """Out-of-range query parameters produce field-specific validation errors."""

    request = RequestFactory().get(
        "/admin/core/odoo-quote-report/",
        {"quote_window_days": "0", "recent_product_limit": "101"},
    )

    with pytest.raises(ValidationError) as exc_info:
        OdooQuoteReportParams.from_request(request)

    assert "Ensure this value is between 1 and 365." in exc_info.value.messages

