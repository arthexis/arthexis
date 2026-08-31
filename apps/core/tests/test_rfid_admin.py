from __future__ import annotations

import pytest
from django.contrib import admin
from django.test import RequestFactory

from apps.cards.models import RFID, RFIDGeneratedLabel
from apps.cards.rfid_names import generated_label_for_rfid, rfid_name_key
from apps.core.admin import rfid as rfid_admin


@pytest.mark.django_db
def test_release_form_uses_generated_card_name(monkeypatch) -> None:
    captured = {}

    class CapturingDocument:
        width = 500

        def __init__(self, *args, **kwargs):
            self.title = ""

        def build(self, story):
            captured["story"] = story

    monkeypatch.setattr(rfid_admin, "SimpleDocTemplate", CapturingDocument)
    tag = RFID.objects.create(label_id=40, rfid="DC476D46B0")
    request = RequestFactory().get("/admin/cards/rfid/")
    request.LANGUAGE_CODE = "en"
    model_admin = rfid_admin.RFIDAdmin(RFID, admin.site)

    response = model_admin._render_release_form(
        request,
        RFID.objects.filter(pk=tag.pk),
        "empty",
        "/admin/cards/rfid/",
    )

    tag.refresh_from_db()
    expected_label = generated_label_for_rfid(tag.rfid)
    tables = [
        item for item in captured["story"] if item.__class__.__name__ == "Table"
    ]
    assert response["Content-Disposition"] == (
        "attachment; filename=rfid-release-form.pdf"
    )
    assert tag.generated_label == expected_label
    assert str(tables[0]._cellvalues[0][0]) == "Card name"
    assert tables[0]._cellvalues[1][0] == expected_label
    assert tables[0]._cellvalues[1][0] != str(tag.pk)


@pytest.mark.django_db
def test_release_form_repairs_stale_generated_card_name(monkeypatch) -> None:
    captured = {}

    class CapturingDocument:
        width = 500

        def __init__(self, *args, **kwargs):
            self.title = ""

        def build(self, story):
            captured["story"] = story

    monkeypatch.setattr(rfid_admin, "SimpleDocTemplate", CapturingDocument)
    tag = RFID.objects.create(label_id=41, rfid="DC476D46B0")
    RFID.objects.filter(pk=tag.pk).update(
        name_key="STALE",
        generated_label="StaleName001",
    )
    request = RequestFactory().get("/admin/cards/rfid/")
    request.LANGUAGE_CODE = "en"
    model_admin = rfid_admin.RFIDAdmin(RFID, admin.site)

    model_admin._render_release_form(
        request,
        RFID.objects.filter(pk=tag.pk),
        "empty",
        "/admin/cards/rfid/",
    )

    tag.refresh_from_db()
    expected_label = generated_label_for_rfid(tag.rfid)
    tables = [
        item for item in captured["story"] if item.__class__.__name__ == "Table"
    ]
    assert tag.name_key == rfid_name_key(tag.rfid)
    assert tag.generated_label == expected_label
    assert tables[0]._cellvalues[1][0] == expected_label


@pytest.mark.django_db
def test_release_form_repairs_stale_generated_card_name_with_same_key(monkeypatch) -> None:
    captured = {}

    class CapturingDocument:
        width = 500

        def __init__(self, *args, **kwargs):
            self.title = ""

        def build(self, story):
            captured["story"] = story

    monkeypatch.setattr(rfid_admin, "SimpleDocTemplate", CapturingDocument)
    tag = RFID.objects.create(label_id=42, rfid="DC476D46B0")
    expected_key = rfid_name_key(tag.rfid)
    RFIDGeneratedLabel.objects.update_or_create(
        name_key=expected_key,
        defaults={"generated_label": "MappedName777"},
    )
    RFID.objects.filter(pk=tag.pk).update(generated_label="OldName111")

    request = RequestFactory().get("/admin/cards/rfid/")
    request.LANGUAGE_CODE = "en"
    model_admin = rfid_admin.RFIDAdmin(RFID, admin.site)

    model_admin._render_release_form(
        request,
        RFID.objects.filter(pk=tag.pk),
        "empty",
        "/admin/cards/rfid/",
    )

    tag.refresh_from_db()
    tables = [
        item for item in captured["story"] if item.__class__.__name__ == "Table"
    ]
    assert tag.generated_label == "MappedName777"
    assert tables[0]._cellvalues[1][0] == "MappedName777"
