from django.test import Client, SimpleTestCase, override_settings


class HealthEndpointTests(SimpleTestCase):
    @override_settings(ALLOWED_HOSTS=["0.0.0.0", "arthexis.com"])
    def test_health_is_dependency_light_and_accepts_configured_host(self) -> None:
        response = Client().get("/health/", HTTP_HOST="arthexis.com")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    @override_settings(ALLOWED_HOSTS=["0.0.0.0"])
    def test_health_rejects_unknown_host(self) -> None:
        response = Client().get("/health/", HTTP_HOST="example.invalid")

        self.assertEqual(response.status_code, 400)
