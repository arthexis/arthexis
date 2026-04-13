"""Tests for generic reference attachment admin integration."""

from __future__ import annotations

import pytest
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory

from apps.cards.models import RFID
from apps.core.admin.rfid import RFIDAdmin
from apps.links.admin import ReferenceAdmin, ReferenceAttachmentInline
from apps.links.models import ExperienceReference
from apps.ocpp.admin.charge_point.admin import ChargerAdmin
from apps.ocpp.models import Charger
from apps.terms.admin import TermAdmin
from apps.terms.models import Term


@pytest.mark.parametrize(
    ("admin_class", "model"),
    (
        (ChargerAdmin, Charger),
        (RFIDAdmin, RFID),
        (TermAdmin, Term),
    ),
)
def test_reference_attachment_inline_is_enabled_for_reference_models(
    admin_class,
    model,
) -> None:
    admin_instance = admin_class(model, AdminSite())
    request = RequestFactory().get("/admin/")

    assert ReferenceAttachmentInline in admin_instance.get_inlines(request, None)


def test_reference_admin_keeps_attachment_context_out_of_reference_form() -> None:
    admin_instance = ReferenceAdmin(ExperienceReference, AdminSite())
    request = RequestFactory().get("/admin/")

    assert ReferenceAttachmentInline not in admin_instance.get_inlines(request, None)
