from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.urls import reverse
from django.utils import timezone

from apps.ocpp.models import Charger, Transaction

pytestmark = [pytest.mark.django_db]


def _create_user(username: str, *, is_staff: bool = False, is_superuser: bool = False):
    return get_user_model().objects.create_user(
        username=username,
        password="pass",
        is_staff=is_staff,
        is_superuser=is_superuser,
    )


def test_energy_reports_requires_staff(client):
    url = reverse("ocpp:energy-reports")

    response = client.get(url)
    assert response.status_code == 302
    assert "/admin/login/" in response["Location"]

    client.force_login(_create_user("energy-report-user"))
    response = client.get(url)
    assert response.status_code == 403


def test_energy_reports_form_defaults_to_last_month(client, monkeypatch):
    today = timezone.localdate()
    client.force_login(_create_user("energy-report-staff", is_staff=True))

    def fake_render(request, template_name, context, status=200):
        del request, template_name
        form = context["form"]
        html = f'value="{form["start"].value()}" ' f'value="{form["end"].value()}"'
        return HttpResponse(html, status=status)

    monkeypatch.setattr("apps.ocpp.views.reports.render", fake_render)

    response = client.get(reverse("ocpp:energy-reports"))

    assert response.status_code == 200
    html = response.content.decode()
    assert f'value="{today:%Y-%m-%d}"' in html
    assert f'value="{today - timedelta(days=30):%Y-%m-%d}"' in html


def test_energy_reports_downloads_visible_charger_transactions_as_csv(client):
    user = _create_user("energy-report-downloader", is_staff=True)
    client.force_login(user)
    start = timezone.make_aware(datetime(2026, 6, 1, 9, 0))
    included = Charger.objects.create(
        charger_id="CP-REPORT-1",
        connector_id=1,
        public_display=False,
    )
    owned_by_user = Charger.objects.create(
        charger_id="CP-REPORT-OWNED",
        connector_id=1,
        public_display=False,
    )
    owned_by_user.owner_users.add(user)
    scoped_to_another_user = Charger.objects.create(
        charger_id="CP-REPORT-HIDDEN", connector_id=1
    )
    scoped_to_another_user.owner_users.add(_create_user("energy-report-owner"))
    out_of_range = Charger.objects.create(
        charger_id="CP-REPORT-OUT-OF-RANGE", connector_id=1
    )
    Transaction.objects.create(
        charger=included,
        connector_id=1,
        start_time=start,
        stop_time=start + timedelta(hours=1),
        meter_start=1000,
        meter_stop=3500,
        ocpp_transaction_id="TX-CSV-1",
        rfid="ABC123",
        vid="VID-1",
    )
    Transaction.objects.create(
        charger=owned_by_user,
        connector_id=1,
        start_time=start,
        stop_time=start + timedelta(hours=1),
        meter_start=0,
        meter_stop=2000,
        ocpp_transaction_id="TX-CSV-OWNED",
    )
    Transaction.objects.create(
        charger=scoped_to_another_user,
        connector_id=1,
        start_time=start,
        stop_time=start + timedelta(hours=1),
        meter_start=0,
        meter_stop=1000,
        ocpp_transaction_id="TX-CSV-HIDDEN",
        rfid="RFID-HIDDEN",
        vid="VID-HIDDEN",
    )
    Transaction.objects.create(
        charger=out_of_range,
        connector_id=1,
        start_time=start - timedelta(days=10),
        stop_time=start - timedelta(days=10, hours=-1),
        meter_start=0,
        meter_stop=1000,
    )

    response = client.get(
        reverse("ocpp:energy-reports"),
        {"download": "1", "start": "2026-06-01", "end": "2026-06-01"},
    )

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    assert "charger-energy-20260601-20260601.csv" in response["Content-Disposition"]
    csv_text = response.content.decode()
    assert "charger_id,connector_id,transaction_id" in csv_text
    assert "CP-REPORT-1,1," in csv_text
    assert "TX-CSV-1" in csv_text
    assert "2.500" in csv_text
    assert "CP-REPORT-OWNED,1," in csv_text
    assert "TX-CSV-OWNED" in csv_text
    assert "CP-REPORT-HIDDEN" not in csv_text
    assert "TX-CSV-HIDDEN" not in csv_text
    assert "RFID-HIDDEN" not in csv_text
    assert "VID-HIDDEN" not in csv_text
    assert "CP-REPORT-OUT-OF-RANGE" not in csv_text


def test_energy_reports_superuser_downloads_hidden_owner_scoped_transactions(client):
    user = _create_user(
        "energy-report-superuser",
        is_staff=True,
        is_superuser=True,
    )
    client.force_login(user)
    start = timezone.make_aware(datetime(2026, 6, 3, 9, 0))
    hidden = Charger.objects.create(
        charger_id="CP-REPORT-SUPERUSER",
        connector_id=1,
        public_display=False,
    )
    hidden.owner_users.add(_create_user("energy-report-private-owner"))
    Transaction.objects.create(
        charger=hidden,
        connector_id=1,
        start_time=start,
        stop_time=start + timedelta(hours=1),
        meter_start=0,
        meter_stop=1000,
        ocpp_transaction_id="TX-CSV-SUPERUSER",
    )

    response = client.get(
        reverse("ocpp:energy-reports"),
        {"download": "1", "start": "2026-06-03", "end": "2026-06-03"},
    )

    csv_text = response.content.decode()
    assert "CP-REPORT-SUPERUSER,1," in csv_text
    assert "TX-CSV-SUPERUSER" in csv_text


def test_energy_reports_sanitizes_formula_like_csv_cells(client):
    client.force_login(_create_user("energy-report-formula", is_staff=True))
    start = timezone.make_aware(datetime(2026, 6, 2, 9, 0))
    charger = Charger.objects.create(charger_id="=CP-FORMULA", connector_id=1)
    Transaction.objects.create(
        charger=charger,
        connector_id=1,
        start_time=start,
        stop_time=start + timedelta(hours=1),
        meter_start=0,
        meter_stop=1000,
        ocpp_transaction_id="\t@TX-FORMULA",
        rfid=" +RFID-FORMULA",
        vid="-VID-FORMULA",
    )

    response = client.get(
        reverse("ocpp:energy-reports"),
        {"download": "1", "start": "2026-06-02", "end": "2026-06-02"},
    )

    csv_text = response.content.decode()
    assert "'=CP-FORMULA" in csv_text
    assert "'\t@TX-FORMULA" in csv_text
    assert "' +RFID-FORMULA" in csv_text
    assert "'-VID-FORMULA" in csv_text


def test_energy_reports_landing_is_seeded_under_charge_points_module():
    fixture_data = json.loads(
        Path("apps/sites/fixtures/default__modules_terminal.json").read_text()
    )

    ocpp_landings = [
        (item["fields"]["path"], item["fields"]["label"])
        for item in fixture_data
        if item.get("model") == "pages.landing"
        and item.get("fields", {}).get("module") == ["/ocpp/"]
    ]

    assert (reverse("ocpp:energy-reports"), "Energy Reports") in ocpp_landings
