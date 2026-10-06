from django.urls import reverse


def test_dashboard_index_renders_operational_context(client):
    response = client.get(reverse("dashboard:index"))

    assert response.status_code == 200
    assert response.templates[0].name == "dashboard/index.html"
    assert response.context["site_name"] == "Plaza La Silla"
    assert response.context["summary"] == {
        "chargers": 12,
        "charging": 9,
        "site_power": "126 kW",
        "offline": 1,
    }
    assert [charger["name"] for charger in response.context["chargers"]] == [
        "A01",
        "A02",
        "A03",
        "B01",
    ]


def test_status_fragment_uses_fragment_template(client):
    response = client.get(reverse("dashboard:status-fragment"))

    assert response.status_code == 200
    assert response.templates[0].name == "dashboard/_charger_status.html"
    assert response.context["summary"]["charging"] == 9
    assert len(response.context["chargers"]) == 4
