from pathlib import Path

import pytest
from django.apps import apps as django_apps
from django.db.models.signals import post_migrate

from apps.app.models import Application
from apps.features import loader


@pytest.mark.django_db
def test_load_feature_seed_data_accepts_post_migrate_kwargs(monkeypatch):
    fixture_path = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "features__ocpp_forwarder.json"
    )
    monkeypatch.setattr(loader, "_fixture_paths", lambda: [fixture_path])
    called = {}

    def _fake_call_command(command, *args, **kwargs):
        called["command"] = command
        called["args"] = args
        called["kwargs"] = kwargs

    monkeypatch.setattr(loader, "call_command", _fake_call_command)
    sender = django_apps.get_app_config("features")

    loader.load_feature_seed_data(
        signal=post_migrate,
        sender=sender,
        app_config=sender,
        verbosity=1,
        interactive=False,
        using="default",
        plan=None,
        apps=django_apps,
    )

    assert Application.objects.filter(name="ocpp").exists()
    assert called["command"] == "loaddata"
    assert called["kwargs"]["database"] == "default"
    assert called["kwargs"]["verbosity"] == 0
    assert called["args"] == ("apps/features/fixtures/features__ocpp_forwarder.json",)
