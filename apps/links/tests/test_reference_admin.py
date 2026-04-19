"""Tests for links reference admin endpoints."""

from __future__ import annotations

import json
import uuid

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from apps.links.admin import ReferenceAdmin
from apps.links.models import Reference


pytestmark = pytest.mark.django_db


def test_bulk_create_keeps_existing_transaction_uuid() -> None:
    existing = Reference.objects.create(
        alt_text="Existing",
        value="https://example.com/existing",
        method="link",
        transaction_uuid=uuid.uuid4(),
    )
    payload_transaction = uuid.uuid4()
    user = get_user_model().objects.create_user(
        username="links-admin",
        password="unused-password",
    )
    request = RequestFactory().post(
        "/admin/links/reference/bulk/",
        data=json.dumps(
            {
                "transaction_uuid": str(payload_transaction),
                "references": [
                    {
                        "alt_text": existing.alt_text,
                        "value": existing.value,
                    }
                ],
            }
        ),
        content_type="application/json",
    )
    request.user = user

    response = ReferenceAdmin(Reference, AdminSite()).bulk_create(request)

    assert response.status_code == 200
    existing.refresh_from_db()
    assert existing.transaction_uuid != payload_transaction
