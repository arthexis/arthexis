"""Tests for Raspberry Pi footer label migration."""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from apps.links.models import Reference


pytestmark = pytest.mark.django_db


RPI4B_VALUE = "https://www.raspberrypi.com/products/raspberry-pi-4-model-b/"


def test_raspberry_pi_footer_label_migration_updates_existing_seed_rows() -> None:
    migration = importlib.import_module(
        "apps.links.migrations.0005_update_raspberry_pi_footer_label"
    )

    stale = Reference.objects.create(
        alt_text="Raspberry Pi 4B 2GB",
        value=RPI4B_VALUE,
        include_in_footer=True,
        is_seed_data=True,
        method="link",
    )
    duplicate = Reference.objects.create(
        alt_text="Raspberry Pi 4B 3GB",
        value=RPI4B_VALUE,
        include_in_footer=True,
        is_seed_data=True,
        method="link",
    )

    migration.update_raspberry_pi_footer_label(
        apps=type("Apps", (), {"get_model": staticmethod(lambda *_: Reference)}),
        schema_editor=SimpleNamespace(connection=SimpleNamespace(alias="default")),
    )

    stale.refresh_from_db()
    assert stale.alt_text == "Raspberry Pi 4B 3GB"
    assert stale.value == RPI4B_VALUE
    assert not Reference.objects.filter(pk=duplicate.pk).exists()


def test_raspberry_pi_footer_label_migration_keeps_existing_non_seed_label() -> None:
    migration = importlib.import_module(
        "apps.links.migrations.0005_update_raspberry_pi_footer_label"
    )

    stale_seed = Reference.objects.create(
        alt_text="Raspberry Pi 4B 2GB",
        value=RPI4B_VALUE,
        include_in_footer=True,
        is_seed_data=True,
        method="link",
    )
    existing_non_seed = Reference.objects.create(
        alt_text="Raspberry Pi 4B 3GB",
        value=RPI4B_VALUE,
        include_in_footer=True,
        is_seed_data=False,
        method="link",
    )

    migration.update_raspberry_pi_footer_label(
        apps=type("Apps", (), {"get_model": staticmethod(lambda *_: Reference)}),
        schema_editor=SimpleNamespace(connection=SimpleNamespace(alias="default")),
    )

    assert not Reference.objects.filter(pk=stale_seed.pk).exists()
    existing_non_seed.refresh_from_db()
    assert existing_non_seed.alt_text == "Raspberry Pi 4B 3GB"


def test_raspberry_pi_footer_label_migration_handles_soft_deleted_target_row() -> None:
    migration = importlib.import_module(
        "apps.links.migrations.0005_update_raspberry_pi_footer_label"
    )

    stale_seed = Reference.objects.create(
        alt_text="Raspberry Pi 4B 2GB",
        value=RPI4B_VALUE,
        include_in_footer=True,
        is_seed_data=True,
        method="link",
    )
    soft_deleted_target = Reference.objects.create(
        alt_text="Raspberry Pi 4B 3GB",
        value=RPI4B_VALUE,
        include_in_footer=True,
        is_seed_data=True,
        method="link",
    )
    Reference.all_objects.filter(pk=soft_deleted_target.pk).update(is_deleted=True)

    migration.update_raspberry_pi_footer_label(
        apps=type("Apps", (), {"get_model": staticmethod(lambda *_: Reference)}),
        schema_editor=SimpleNamespace(connection=SimpleNamespace(alias="default")),
    )

    stale_seed.refresh_from_db()
    assert stale_seed.alt_text == "Raspberry Pi 4B 3GB"
    assert not Reference.all_objects.filter(pk=soft_deleted_target.pk).exists()
