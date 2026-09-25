from django.test import Client


def test_health_endpoint_is_available() -> None:
    response = Client().get("/ocpp/health/")

    assert response.status_code == 200
    assert response.json()["version"] == "2.0"
