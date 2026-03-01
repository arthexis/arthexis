"""Tests for manifest-based Django app discovery in settings."""

from __future__ import annotations

import pytest

from config import settings

pytestmark = pytest.mark.critical


EXPECTED_LOCAL_APPS = [
    "apps.base",
    "apps.blog",
    "apps.credentials",
    "apps.actions",
    "apps.celery",
    "apps.nodes",
    "apps.discovery",
    "apps.ftp",
    "apps.dns",
    "apps.screens",
    "apps.sensors",
    "apps.pyxel",
    "apps.counters",
    "apps.energy",
    "apps.groups",
    "apps.core",
    "apps.graphql",
    "apps.mcp",
    "apps.users",
    "apps.leads",
    "apps.embeds",
    "apps.flows",
    "apps.release",
    "apps.emails",
    "apps.extensions",
    "apps.desktop",
    "apps.payments",
    "apps.sponsors",
    "apps.links",
    "apps.docs",
    "apps.gdrive",
    "apps.calendars",
    "apps.maps",
    "apps.locals",
    "apps.locale",
    "apps.content",
    "apps.clocks",
    "apps.audio",
    "apps.bluetooth",
    "apps.video",
    "apps.media",
    "apps.mermaid",
    "apps.odoo",
    "apps.sigils",
    "apps.selenium",
    "apps.repos",
    "apps.reports",
    "apps.app",
    "apps.rates",
    "apps.vehicle",
    "apps.protocols",
    "apps.ocpp",
    "apps.ocpp.forwarder",
    "apps.simulators",
    "apps.meta",
    "apps.awg",
    "apps.chats",
    "apps.aws",
    "apps.alexa",
    "apps.socials",
    "apps.ops",
    "apps.survey",
    "apps.modules",
    "apps.features",
    "apps.prompts",
    "apps.widgets",
    "apps.apis",
    "apps.sites",
    "apps.smb",
    "apps.summary",
    "apps.services",
    "apps.certs",
    "apps.nginx",
    "apps.cards",
    "apps.nfts",
    "apps.tasks",
    "apps.recipes",
    "apps.tests",
    "apps.teams",
    "apps.logbook",
    "apps.nmcli",
    "apps.wikis",
    "apps.totp",
    "apps.terms",
    "apps.evergo",
    "apps.fitbit",
]


def test_local_apps_manifest_loading_is_complete_and_deterministic() -> None:
    """Manifest loading should reproduce the project app list deterministically."""

    first_load = settings._load_local_apps_from_manifests()
    second_load = settings._load_local_apps_from_manifests()

    assert set(first_load) == set(EXPECTED_LOCAL_APPS)
    assert set(second_load) == set(EXPECTED_LOCAL_APPS)
    assert first_load == second_load
    assert len(first_load) == len(set(first_load))


def test_local_apps_manifest_loading_does_not_import_app_configs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Loading manifests for LOCAL_APPS should not validate/import app configs eagerly."""

    def _fail_if_called(_app_entry: str) -> None:
        raise AssertionError(
            "_validate_manifest_app_entry should not run during manifest loading"
        )

    monkeypatch.setattr(settings, "_validate_manifest_app_entry", _fail_if_called)

    loaded_apps = settings._load_local_apps_from_manifests()

    assert loaded_apps


def test_local_apps_manifests_resolve_to_importable_app_configs() -> None:
    """Every loaded manifest entry should resolve through AppConfig.create."""

    for app_entry in settings._load_local_apps_from_manifests():
        settings._validate_manifest_app_entry(app_entry)
