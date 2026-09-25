from django.test import Client, override_settings


@override_settings(ALLOWED_HOSTS=["0.0.0.0", "arthexis.com"])
def test_health_is_dependency_light_and_accepts_configured_host() -> None:
    response = Client().get("/health/", HTTP_HOST="arthexis.com")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@override_settings(ALLOWED_HOSTS=["0.0.0.0"])
def test_health_rejects_unknown_host() -> None:
    response = Client().get("/health/", HTTP_HOST="example.invalid")

    assert response.status_code == 400
