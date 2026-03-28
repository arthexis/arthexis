from pathlib import Path

import pytest
from django.apps import apps as django_apps
from django.contrib.sites.models import Site
from django.db.models.signals import post_migrate

from apps.sites import loader
from apps.sites.models import SiteBadge


@pytest.mark.django_db
def test_load_admin_badge_seed_data_accepts_post_migrate_kwargs(monkeypatch):
    fixture_path = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "admin_badges__defaults.json"
    )
    monkeypatch.setattr(loader, "_admin_badge_fixture_paths", lambda: [fixture_path])
    called = {}

    def _fake_call_command(command, *args, **kwargs):
        called["command"] = command
        called["args"] = args
        called["kwargs"] = kwargs

    monkeypatch.setattr(loader, "call_command", _fake_call_command)
    sender = django_apps.get_app_config("pages")

    loader.load_admin_badge_seed_data(
        signal=post_migrate,
        sender=sender,
        app_config=sender,
        verbosity=1,
        interactive=False,
        using="default",
        plan=None,
        apps=django_apps,
    )

    assert called["command"] == "loaddata"
    assert called["kwargs"]["database"] == "default"
    assert called["kwargs"]["verbosity"] == 0
    assert called["args"] == ("apps/sites/fixtures/admin_badges__defaults.json",)


@pytest.mark.django_db
def test_ensure_site_badges_exist_creates_missing_badges():
    site = Site.objects.create(domain="seeded.local", name="Seeded Site")

    loader.ensure_site_badges_exist(using="default")

    assert SiteBadge.objects.filter(site=site, is_seed_data=True).exists()
