"""Regression tests for Evergo customer admin views."""

from __future__ import annotations

import csv
import io

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.evergo.models import EvergoCustomer, EvergoUser

@pytest.mark.django_db
def test_evergo_customer_export_defaults_to_no_header_row(client):
    """CSV exports should omit headers unless include header is explicitly enabled."""
    User = get_user_model()
    admin_user = User.objects.create_superuser(
        username="evergo-export-admin",
        email="evergo-export-admin@example.com",
        password="top-secret",  # noqa: S106
    )
    profile = EvergoUser.objects.create(
        user=admin_user,
        evergo_email="contractor@example.com",
        evergo_password="top-secret",  # noqa: S106
    )
    customer = EvergoCustomer.objects.create(user=profile, name="No Header Customer")

    client.force_login(admin_user)
    response = client.post(
        reverse("admin:evergo_evergocustomer_export"),
        data={
            "format": "csv",
            "export_columns": ["name"],
            "export_scope_selected": "on",
            "selected": [str(customer.pk)],
        },
    )

    assert response.status_code == 200
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8"))))
    assert rows == [["No Header Customer"]]

