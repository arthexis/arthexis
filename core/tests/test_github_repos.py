from __future__ import annotations

import os

import django
import requests
from django.test import TestCase
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from core import github_repos


class CreateRepositoryTests(TestCase):
    def test_successful_creation_for_organisation(self) -> None:
        response = mock.Mock()
        response.status_code = 201

        with mock.patch(
            "core.github_repos.get_github_token", return_value="token"
        ) as get_token:
            with mock.patch("requests.post", return_value=response) as post:
                with self.assertLogs("core.github_repos", level="INFO") as logs:
                    result = github_repos.create_repository(
                        owner="example-org",
                        repo="demo",
                        visibility="public",
                        description="Demo repository",
                    )

        self.assertIs(result, response)
        get_token.assert_called_once_with()
        post.assert_called_once()
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://api.github.com/orgs/example-org/repos")
        self.assertEqual(kwargs["timeout"], github_repos.REQUEST_TIMEOUT)
        self.assertEqual(kwargs["json"], {
            "name": "demo",
            "visibility": "public",
            "description": "Demo repository",
        })
        self.assertEqual(kwargs["headers"]["Authorization"], "token token")
        self.assertTrue(
            any("GitHub repository created" in message for message in logs.output)
        )

    def test_user_repository_creation_uses_user_endpoint(self) -> None:
        response = mock.Mock()
        response.status_code = 201

        with mock.patch("core.github_repos.get_github_token", return_value="token"):
            with mock.patch("requests.post", return_value=response) as post:
                github_repos.create_repository(
                    owner=None,
                    repo="demo",
                    visibility="private",
                    description=None,
                )

        post.assert_called_once()
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://api.github.com/user/repos")
        self.assertNotIn("description", kwargs["json"])

    def test_failure_logs_and_raises(self) -> None:
        response = mock.Mock()
        response.status_code = 422
        response.text = "invalid"
        response.raise_for_status.side_effect = requests.HTTPError("invalid")

        with mock.patch("core.github_repos.get_github_token", return_value="token"):
            with mock.patch("requests.post", return_value=response):
                with self.assertLogs("core.github_repos", level="ERROR") as logs:
                    with self.assertRaises(requests.HTTPError):
                        github_repos.create_repository(
                            owner="example-org",
                            repo="demo",
                            visibility="private",
                        )

        self.assertTrue(
            any("GitHub repository creation failed" in message for message in logs.output)
        )
