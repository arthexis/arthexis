import os
import subprocess
import sys
from unittest import TestCase

from django.test import Client, override_settings


class HealthEndpointTests(TestCase):
    @override_settings(ALLOWED_HOSTS=["0.0.0.0", "arthexis.com"])
    def test_health_is_dependency_light_and_accepts_configured_host(self) -> None:
        response = Client().get("/health/", HTTP_HOST="arthexis.com")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    @override_settings(ALLOWED_HOSTS=["0.0.0.0"])
    def test_health_rejects_unknown_host(self) -> None:
        response = Client().get("/health/", HTTP_HOST="example.invalid")

        self.assertEqual(response.status_code, 400)


class AllowedHostsEnvironmentTests(TestCase):
    def test_default_allowed_host_is_zero_address(self) -> None:
        env = os.environ.copy()
        env.pop("ARTHEXIS_ALLOWED_HOSTS", None)
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import arthexis.settings as s; print(','.join(s.ALLOWED_HOSTS))",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )

        self.assertEqual(result.stdout.strip(), "0.0.0.0")

    def test_allowed_hosts_are_read_from_comma_separated_environment(self) -> None:
        env = os.environ.copy()
        env["ARTHEXIS_ALLOWED_HOSTS"] = "0.0.0.0, arthexis.com"
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import arthexis.settings as s; print(','.join(s.ALLOWED_HOSTS))",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )

        self.assertEqual(result.stdout.strip(), "0.0.0.0,arthexis.com")
