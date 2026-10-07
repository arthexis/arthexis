from django.urls import reverse

from dashboard import views


STATUS_DATA = {
    "chargers": [
        {
            "id": "CP001",
            "connected": True,
            "status": "Charging",
            "connectors": [{"id": 1, "status": "Charging"}],
        },
        {
            "id": "CP002",
            "connected": False,
            "status": None,
            "connectors": [],
        },
    ]
}


def context():
    return {
        "site_name": "Local appliance",
        "integration_available": True,
        "energy_available": True,
        "chargers": [
            {
                "name": "CP001",
                "status": "Charging",
                "power": "18.6 kW",
                "detail": "Connector 1",
                "tone": "emerald",
            },
            {
                "name": "CP002",
                "status": "Offline",
                "power": "—",
                "detail": "Disconnected",
                "tone": "rose",
            },
        ],
        "summary": {
            "chargers": 2,
            "charging": 1,
            "site_power": "18.6 kW",
            "offline": 1,
            "energy": "428.7 kWh",
            "transactions": 12,
        },
    }


def test_dashboard_index_renders_operational_context(client, monkeypatch):
    monkeypatch.setattr(views, "dashboard_context", context)

    response = client.get(reverse("dashboard:index"))

    assert response.status_code == 200
    assert response.templates[0].name == "dashboard/index.html"
    assert response.context["site_name"] == "Local appliance"
    assert response.context["summary"]["chargers"] == 2
    assert response.context["summary"]["site_power"] == "18.6 kW"
    assert b"428.7 kWh" in response.content
    assert [charger["name"] for charger in response.context["chargers"]] == [
        "CP001",
        "CP002",
    ]


def test_status_fragment_uses_fragment_template(client, monkeypatch):
    monkeypatch.setattr(views, "dashboard_context", context)

    response = client.get(reverse("dashboard:status-fragment"))

    assert response.status_code == 200
    assert response.templates[0].name == "dashboard/_charger_status.html"
    assert response.context["summary"]["charging"] == 1
    assert len(response.context["chargers"]) == 2
    assert b"18.6 kW" in response.content


def test_status_fragment_shows_csms_unavailable(client, monkeypatch):
    monkeypatch.setattr(
        views,
        "dashboard_context",
        lambda: {
            "site_name": "Local appliance",
            "integration_available": False,
            "energy_available": False,
            "chargers": [],
            "summary": {
                "chargers": 0,
                "charging": 0,
                "site_power": "—",
                "offline": 0,
                "energy": "—",
                "transactions": 0,
            },
        },
    )

    response = client.get(reverse("dashboard:status-fragment"))

    assert response.status_code == 200
    assert b"OCPP-CSMS status is unavailable." in response.content
