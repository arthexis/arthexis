import os
from pathlib import Path
from unittest import mock

from django.test import TestCase

from core import release


class PyPITokenTests(TestCase):
    def test_publish_uses_token_when_password_missing(self):
        creds = release.Credentials(
            token="pypi-token", username="ignored", password=None
        )
        with (
            mock.patch("core.release.network_available", return_value=False),
            mock.patch.object(release.Path, "exists", return_value=True),
            mock.patch.object(
                release.Path, "glob", return_value=[Path("dist/fake.whl")]
            ),
            mock.patch("core.release.subprocess.run") as run,
        ):
            run.return_value.returncode = 0
            run.return_value.stdout = ""
            run.return_value.stderr = ""
            release.publish(version="0.1.1", creds=creds)
        cmd = run.call_args[0][0]
        assert "__token__" in cmd
        assert "pypi-token" in cmd
        assert "ignored" not in cmd

    def test_publish_prefers_profile_over_env(self):
        profile = release.Credentials(token="profile-token")
        env = {
            "PYPI_API_TOKEN": "env-token",
            "PYPI_USERNAME": "env-user",
            "PYPI_PASSWORD": "env-pass",
        }
        with (
            mock.patch.dict(os.environ, env, clear=False),
            mock.patch("core.release.network_available", return_value=False),
            mock.patch.object(release.Path, "exists", return_value=True),
            mock.patch.object(
                release.Path, "glob", return_value=[Path("dist/fake.whl")]
            ),
            mock.patch("core.release.subprocess.run") as run,
            mock.patch("core.release._manager_credentials", return_value=profile),
        ):
            run.return_value.returncode = 0
            run.return_value.stdout = ""
            run.return_value.stderr = ""
            release.publish(version="0.1.1")
        cmd = run.call_args[0][0]
        assert "__token__" in cmd
        assert "profile-token" in cmd
        assert "env-user" not in cmd
        assert "env-pass" not in cmd
        assert "env-token" not in cmd
