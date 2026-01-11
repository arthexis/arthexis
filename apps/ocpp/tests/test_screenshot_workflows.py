from decimal import Decimal
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.content.utils import save_screenshot
from apps.ocpp.models import Charger, MeterValue, Transaction


pytestmark = pytest.mark.django_db


def _make_fake_screenshot(path: Path, label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (640, 360), color="navy")
    draw = ImageDraw.Draw(image)
    draw.text((10, 10), label, fill="white")
    image.save(path)


def test_dashboard_snapshot_after_simulated_session(client, settings, monkeypatch):
    monkeypatch.setattr(
        "apps.content.classifiers.registry.run_default_classifiers", lambda sample: []
    )

    user = get_user_model().objects.create_user(
        username="snapshot-user", email="snapshots@example.com", password="pass"
    )
    client.force_login(user)

    charger = Charger.objects.create(
        charger_id="SNAP-CP-1",
        last_heartbeat=timezone.now(),
        last_status="Available",
    )
    Transaction.objects.create(
        charger=charger,
        connector_id=1,
        start_time=timezone.now(),
        stop_time=timezone.now(),
    )

    response = client.get(reverse("ocpp:ocpp-dashboard"))
    assert response.status_code == 200

    screenshot_path = settings.LOG_DIR / "screenshots" / "ocpp-dashboard.png"
    _make_fake_screenshot(screenshot_path, "OCPP Dashboard Snapshot")

    sample = save_screenshot(
        screenshot_path, method="TEST:OCPP Dashboard", link_duplicates=True
    )

    assert sample is not None
    assert sample.method == "TEST:OCPP Dashboard"


def test_evcs_public_site_snapshot_with_transactions(client, settings, monkeypatch):
    monkeypatch.setattr(
        "apps.content.classifiers.registry.run_default_classifiers", lambda sample: []
    )

    charger = Charger.objects.create(
        charger_id="SNAP-CP-2",
        connector_id=1,
        last_heartbeat=timezone.now(),
        last_status="Charging",
    )

    tx = Transaction.objects.create(
        charger=charger,
        connector_id=1,
        start_time=timezone.now(),
        stop_time=timezone.now(),
    )
    MeterValue.objects.create(
        charger=charger,
        transaction=tx,
        connector_id=1,
        timestamp=timezone.now(),
        context="Sample.Periodic",
        energy=Decimal("5.000"),
    )

    response = client.get(reverse("ocpp:charger-page", args=[charger.charger_id]))
    assert response.status_code == 200

    screenshot_path = settings.LOG_DIR / "screenshots" / "evcs-public.png"
    _make_fake_screenshot(screenshot_path, "EVCS Public Snapshot")

    sample = save_screenshot(
        screenshot_path, method="TEST:EVCS Public", link_duplicates=True
    )

    assert sample is not None
    assert sample.method == "TEST:EVCS Public"
