from django.test import SimpleTestCase


class OcppHttpBoundaryTests(SimpleTestCase):
    def test_health_endpoint_is_available(self) -> None:
        response = self.client.get("/ocpp/health/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["version"], "2.0")
