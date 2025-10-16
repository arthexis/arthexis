import os
import subprocess
import sys
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
        commands = [call.args[0] for call in run.call_args_list]
        twine_cmd = next(cmd for cmd in commands if "twine" in cmd)
        assert "__token__" in twine_cmd
        assert "pypi-token" in twine_cmd
        assert "ignored" not in twine_cmd
        assert ["git", "tag", "v0.1.1"] in commands
        assert ["git", "push", "origin", "v0.1.1"] in commands

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
        commands = [call.args[0] for call in run.call_args_list]
        twine_cmd = next(cmd for cmd in commands if "twine" in cmd)
        assert "__token__" in twine_cmd
        assert "profile-token" in twine_cmd
        assert "env-user" not in twine_cmd
        assert "env-pass" not in twine_cmd
        assert "env-token" not in twine_cmd
        assert ["git", "tag", "v0.1.1"] in commands
        assert ["git", "push", "origin", "v0.1.1"] in commands

    def test_publish_raises_when_dist_missing(self):
        with (
            mock.patch("core.release.network_available", return_value=False),
            mock.patch.object(release.Path, "exists", return_value=False),
        ):
            with self.assertRaises(release.ReleaseError) as exc:
                release.publish(version="0.1.1")
        assert str(exc.exception) == "dist directory not found"

    def test_publish_raises_when_dist_empty(self):
        with (
            mock.patch("core.release.network_available", return_value=False),
            mock.patch.object(release.Path, "exists", return_value=True),
            mock.patch.object(release.Path, "glob", return_value=[]),
        ):
            with self.assertRaises(release.ReleaseError) as exc:
                release.publish(version="0.1.1")
        assert str(exc.exception) == "dist directory is empty"

    def test_publish_raises_when_credentials_missing(self):
        with (
            mock.patch("core.release.network_available", return_value=False),
            mock.patch.object(release.Path, "exists", return_value=True),
            mock.patch.object(
                release.Path, "glob", return_value=[Path("dist/fake.whl")]
            ),
        ):
            with self.assertRaises(release.ReleaseError) as exc:
                release.publish(version="0.1.1", creds=release.Credentials())
        assert str(exc.exception) == "Missing PyPI credentials"

    def test_publish_raises_when_version_already_available(self):
        response = mock.Mock()
        response.ok = True
        response.json.return_value = {"releases": {"0.1.1": [{}]}}

        primary = release.RepositoryTarget(
            name="PyPI",
            verify_availability=True,
            credentials=release.Credentials(token="pypi-token"),
        )

        with (
            mock.patch("core.release.network_available", return_value=True),
            mock.patch.object(release.Path, "exists", return_value=True),
            mock.patch.object(
                release.Path, "glob", return_value=[Path("dist/fake.whl")]
            ),
            mock.patch("requests.get", return_value=response) as request_get,
            mock.patch("core.release.subprocess.run") as run,
        ):
            run.return_value.returncode = 0
            run.return_value.stdout = ""
            run.return_value.stderr = ""
            with self.assertRaises(release.ReleaseError) as exc:
                release.publish(version="0.1.1", repositories=[primary])

        assert str(exc.exception) == "Version 0.1.1 already on PyPI"
        request_get.assert_called_once_with("https://pypi.org/pypi/arthexis/json")
        response.json.assert_called_once()

    def test_publish_skips_availability_check_when_disabled(self):
        response = mock.Mock()
        response.ok = True
        response.json.return_value = {"releases": {"0.1.1": [{}]}}

        primary = release.RepositoryTarget(
            name="PyPI",
            verify_availability=False,
            credentials=release.Credentials(token="pypi-token"),
        )

        with (
            mock.patch("core.release.network_available", return_value=True),
            mock.patch.object(release.Path, "exists", return_value=True),
            mock.patch.object(
                release.Path, "glob", return_value=[Path("dist/fake.whl")]
            ),
            mock.patch("requests.get", return_value=response) as request_get,
            mock.patch("core.release.subprocess.run") as run,
        ):
            run.return_value.returncode = 0
            run.return_value.stdout = ""
            run.return_value.stderr = ""
            uploaded = release.publish(version="0.1.1", repositories=[primary])

        assert request_get.call_count == 0
        assert uploaded == ["PyPI"]

    def test_publish_supports_multiple_repositories(self):
        primary = release.RepositoryTarget(
            name="PyPI",
            verify_availability=True,
            credentials=release.Credentials(token="pypi-token"),
        )
        secondary = release.RepositoryTarget(
            name="GitHub Packages",
            repository_url="https://upload.github.com/pypi/",
            credentials=release.Credentials(
                username="octocat", password="gh-token"
            ),
        )
        package = release.Package(
            name="pkg",
            description="desc",
            author="author",
            email="author@example.com",
            python_requires=">=3.10",
            license="GPL",
        )

        def fake_run(cmd, capture_output=False, text=False, check=True, cwd=None):
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with (
            mock.patch("core.release.network_available", return_value=False),
            mock.patch.object(release.Path, "exists", return_value=True),
            mock.patch.object(
                release.Path, "glob", return_value=[Path("dist/fake.whl")]
            ),
            mock.patch("core.release.subprocess.run", side_effect=fake_run) as run,
        ):
            release.publish(
                package=package,
                version="0.1.1",
                repositories=[primary, secondary],
            )

        commands = [call.args[0] for call in run.call_args_list]
        twine_commands = [cmd for cmd in commands if "twine" in cmd]
        assert len(twine_commands) == 2
        assert twine_commands[0][:4] == [sys.executable, "-m", "twine", "upload"]
        assert "--repository-url" not in twine_commands[0]
        assert "--repository-url" in twine_commands[1]
        assert "https://upload.github.com/pypi/" in twine_commands[1]

    def test_publish_emits_warning_for_git_authentication_errors(self):
        creds = release.Credentials(token="pypi-token")
        error = subprocess.CalledProcessError(
            128,
            ["git", "push", "origin", "v0.1.1"],
            stderr="fatal: could not read Username for 'https://github.com': No such device or address",
        )

        def fake_run(cmd, check=True, cwd=None):
            if cmd[:3] == ["git", "tag", "v0.1.1"]:
                return subprocess.CompletedProcess(cmd, 0)
            if cmd == ["git", "push", "origin", "v0.1.1"]:
                raise error
            raise AssertionError(f"Unexpected command: {cmd}")

        with (
            mock.patch("core.release.network_available", return_value=False),
            mock.patch.object(release.Path, "exists", return_value=True),
            mock.patch.object(
                release.Path, "glob", return_value=[Path("dist/fake.whl")]
            ),
            mock.patch("core.release._upload_with_retries"),
            mock.patch("core.release._manager_git_credentials", return_value=None),
            mock.patch("core.release._run", side_effect=fake_run),
        ):
            with self.assertRaises(release.PostPublishWarning) as exc:
                release.publish(version="0.1.1", creds=creds)

        warning = exc.exception
        message = str(warning)
        assert warning.uploaded == ["PyPI"]
        assert "Upload to PyPI completed" in message
        assert "git push origin v0.1.1" in message
        assert "could not read Username" in message
        assert any(
            "Push git tag v0.1.1 to origin" in note for note in warning.followups
        )

    def test_publish_retries_git_push_with_release_manager_credentials(self):
        creds = release.Credentials(token="pypi-token")
        git_creds = release.GitCredentials(username="octocat", password="gh-token")
        error = subprocess.CalledProcessError(
            128,
            ["git", "push", "origin", "v0.1.1"],
            stderr="fatal: could not read Username for 'https://github.com': No such device or address",
        )
        commands = []

        def fake_run(cmd, check=True, cwd=None):
            commands.append(cmd)
            if cmd[:3] == ["git", "tag", "v0.1.1"]:
                return subprocess.CompletedProcess(cmd, 0)
            if cmd == ["git", "push", "origin", "v0.1.1"]:
                raise error
            if cmd == ["git", "push", "https://auth.example/repo", "v0.1.1"]:
                return subprocess.CompletedProcess(cmd, 0)
            raise AssertionError(f"Unexpected command: {cmd}")

        with (
            mock.patch("core.release.network_available", return_value=False),
            mock.patch.object(release.Path, "exists", return_value=True),
            mock.patch.object(
                release.Path, "glob", return_value=[Path("dist/fake.whl")]
            ),
            mock.patch("core.release._upload_with_retries"),
            mock.patch("core.release._run", side_effect=fake_run),
            mock.patch("core.release._manager_git_credentials", return_value=git_creds),
            mock.patch(
                "core.release._git_remote_url",
                return_value="https://github.com/arthexis/arthexis.git",
            ),
            mock.patch(
                "core.release._remote_with_credentials",
                return_value="https://auth.example/repo",
            ) as remote_with_credentials,
        ):
            uploaded = release.publish(version="0.1.1", creds=creds)

        assert uploaded == ["PyPI"]
        assert ["git", "push", "origin", "v0.1.1"] in commands
        assert ["git", "push", "https://auth.example/repo", "v0.1.1"] in commands
        remote_with_credentials.assert_called_once_with(
            "https://github.com/arthexis/arthexis.git", git_creds
        )
