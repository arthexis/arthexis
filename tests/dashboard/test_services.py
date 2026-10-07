from integrations.ocpp_csms import OcppCsmsUnavailable

from dashboard.services import charger_view, dashboard_context


class StubClient:
    def __init__(self, payload=None, error=None):
        self.payload = payload or {}
        self.error = error

    def status(self):
        if self.error is not None:
            raise self.error
        return self.payload


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
        }
    )

    assert charger == {
        "name": "CP001",
        "status": "Charging",
        "power": "—",
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
        }
    )

    assert charger["status"] == "Offline"
    assert charger["detail"] == "Disconnected"
    assert charger["tone"] == "rose"


def test_dashboard_context_uses_status_contract():
    context = dashboard_context(
        StubClient(
            {
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
            }
        )
    )

    assert context["integration_available"] is True
    assert context["summary"] == {
        "chargers": 3,
        "charging": 1,
        "site_power": "—",
        "offline": 1,
    }
    assert [charger["name"] for charger in context["chargers"]] == [
        "CP001",
        "CP002",
        "CP003",
    ]


def test_dashboard_context_degrades_when_csms_is_unavailable():
    context = dashboard_context(
        StubClient(error=OcppCsmsUnavailable("missing"))
    )

    assert context["integration_available"] is False
    assert context["chargers"] == []
    assert context["summary"] == {
        "chargers": 0,
        "charging": 0,
        "site_power": "—",
        "offline": 0,
    }
