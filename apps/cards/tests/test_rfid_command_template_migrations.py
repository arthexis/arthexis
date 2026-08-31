from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from apps.cards.models import RFIDCommandTemplate

pytestmark = [pytest.mark.django_db]


def test_legacy_kiosk_command_template_migration_normalizes_rows():
    old_kiosk = RFIDCommandTemplate.objects.create(
        name="OLD KIOSK",
        slug="old-kiosk",
        command_name="SUITE_COMMAND",
        source=RFIDCommandTemplate.Source.CUSTOM,
        view_kind="kiosk",
        qr_target_path="https://suite.example/kiosk/card/",
    )
    kiosk_qr = RFIDCommandTemplate.objects.create(
        name="KIOSK QR",
        slug="kiosk-qr",
        command_name="SUITE_COMMAND",
        source=RFIDCommandTemplate.Source.CUSTOM,
        qr_target_path="/kiosk/",
    )
    safe_qr = RFIDCommandTemplate.objects.create(
        name="SAFE QR",
        slug="safe-qr",
        command_name="SUITE_COMMAND",
        source=RFIDCommandTemplate.Source.CUSTOM,
        qr_target_path="/imager/burn/",
    )
    migration_module = importlib.import_module(
        "apps.cards.migrations.0004_retire_legacy_kiosk_command_templates"
    )

    migration_module.retire_legacy_kiosk_command_templates(
        SimpleNamespace(
            get_model=lambda app_label, model_name: RFIDCommandTemplate,
        ),
        None,
    )

    old_kiosk.refresh_from_db()
    kiosk_qr.refresh_from_db()
    safe_qr.refresh_from_db()
    assert old_kiosk.view_kind == RFIDCommandTemplate.ViewKind.GENERAL
    assert old_kiosk.qr_target_path == ""
    assert kiosk_qr.qr_target_path == ""
    assert safe_qr.qr_target_path == "/imager/burn/"
