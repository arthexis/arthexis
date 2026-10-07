from integrations.ocpp_csms import OcppCsmsUnavailable

from dashboard.services import charger_view, dashboard_context


class StubClient:
    def __init__(self, status_payload=None, energy_payload=None, charger_energy=None, error=None, energy_error=None):
        self.status_payload = status_payload or {}
        self.energy_payload = energy_payload or {}
        self.charger_energy = charger_energy or {}
        self.error = error
        self.energy_error = energy_error
        self.energy_calls = []

    def status(self):
        if self.error is not None:
            raise self.error
        return self.status_payload

    def energy(self, *, charger=None):
        self.energy_calls.append(charger)
        if self.energy_error is not None:
            raise self.energy_error
        if charger is None:
            return self.energy_payload
        return self.charger_energy.get(charger, {"summary": {}})


def test_charger_view_prefers_charging_connector():
    charger = charger_view(
        {
            "id": "CP001",
            "connected": True,
            "status": "Charging",
            "connectors": [
                {"id": 1, "status": "Available"},
                {"id": 2, "status": "Charging"},
            ],
        },
        power_w=18600,
    )

    assert charger == {
        "name": "CP001",
        "status": "Charging",
        "power": "18.6 kW",
        "detail": "Connector 2",
        "tone": "emerald",
    }


def test_disconnected_charger_is_presented_offline():
    charger = charger_view(
        {
            "id": "CP002",
            "connected": False,
            "status": "Available",
            "connectors": [{"id": 1, "status": "Available"}],
        },
        power_w=7000,
    )

    assert charger["status"] == "Offline"
    assert charger["power"] == "—"
    assert charger["detail"] == "Disconnected"
    assert charger["tone"] == "rose"


def test_dashboard_context_uses_status_and_energy_contracts():
    client = StubClient(
        status_payload={
            "chargers": [
                {
                    "id": "CP001",
                    "connected": True,
                    "status": "Charging",
                    "connectors": [{"id": 1, "status": "Charging"}],
                },
                {
                    "id": "CP002",
                    "connected": True,
                    "status": "Available",
                    "connectors": [{"id": 1, "status": "Available"}],
                },
                {
                    "id": "CP003",
                    "connected": False,
                    "status": None,
                    "connectors": [],
                },
            ]
        },
        energy_payload={
            "summary": {
                "current_power_w": 18600,
                "energy_wh": 428700,
                "transactions": 12,
            }
        },
        charger_energy={
            "CP001": {"summary": {"current_power_w": 18600}},
            "CP002": {"summary": {"current_power_w": None}},
        },
    )

    context = dashboard_context(client)

    assert context["integration_available"] is True
    assert context["energy_available"] is True
    assert context["summary"] == {
        "chargers": 3,
        "charging": 1,
        "site_power": "18.6 kW",
        "offline": 1,
        "energy": "428.7 kWh",
        "transactions": 12,
    }
    assert [charger["name"] for charger in context["chargers"]] == [
        "CP001",
        "CP002",
        "CP003",
    ]
    assert [charger["power"] for charger in context["chargers"]] == [
        "18.6 kW",
        "—",
        "—",
    ]
    assert client.energy_calls == [None, "CP001", "CP002"]


def test_dashboard_context_keeps_status_when_energy_is_unavailable():
    context = dashboard_context(
        StubClient(
            status_payload={
                "chargers": [
                    {
                        "id": "CP001",
                        "connected": True,
                        "status": "Charging",
                        "connectors": [{"id": 1, "status": "Charging"}],
                    }
                ]
            },
            energy_error=OcppCsmsUnavailable("energy unavailable"),
        )
    )

    assert context["integration_available"] is True
    assert context["energy_available"] is False
    assert context["chargers"][0]["power"] == "—"
    assert context["summary"]["site_power"] == "—"
    assert context["summary"]["energy"] == "—"


def test_dashboard_context_degrades_when_csms_is_unavailable():
    context = dashboard_context(
        StubClient(error=OcppCsmsUnavailable("missing"))
    )

    assert context["integration_available"] is False
    assert context["energy_available"] is False
    assert context["chargers"] == []
    assert context["summary"] == {
        "chargers": 0,
        "charging": 0,
        "site_power": "—",
        "offline": 0,
        "energy": "—",
        "transactions": 0,
    }
