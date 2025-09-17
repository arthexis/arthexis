from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from unittest import mock

import requests
from django.test import TestCase

from core import github_issues
from core.models import Package
from core.tasks import report_runtime_issue


class ResolveRepositoryTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        Package.objects.all().delete()

    def test_active_package_repository_is_used(self) -> None:
        Package.objects.create(
            name="custom",
            repository_url="https://github.com/example/project.git",
            is_active=True,
        )

        owner, repo = github_issues.resolve_repository()

        self.assertEqual(owner, "example")
        self.assertEqual(repo, "project")

    def test_default_repository_is_used_as_fallback(self) -> None:
        owner, repo = github_issues.resolve_repository()

        self.assertEqual(owner, "arthexis")
        self.assertEqual(repo, "arthexis")


class TokenLookupTests(TestCase):
    def test_token_comes_from_latest_release(self) -> None:
        release = mock.Mock()
        release.get_github_token.return_value = "release-token"

        with mock.patch("core.github_issues.PackageRelease.latest", return_value=release):
            with mock.patch.dict(os.environ, {}, clear=True):
                token = github_issues.get_github_token()

        self.assertEqual(token, "release-token")
        release.get_github_token.assert_called_once_with()

    def test_token_falls_back_to_environment(self) -> None:
        with mock.patch("core.github_issues.PackageRelease.latest", return_value=None):
            with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "env-token"}, clear=True):
                token = github_issues.get_github_token()

        self.assertEqual(token, "env-token")

    def test_missing_token_raises(self) -> None:
        with mock.patch("core.github_issues.PackageRelease.latest", return_value=None):
            with mock.patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(RuntimeError):
                    github_issues.get_github_token()


class FingerprintTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.lock_patch = mock.patch("core.github_issues.LOCK_DIR", Path(self.tempdir.name))
        self.lock_patch.start()
        self.addCleanup(self.lock_patch.stop)

    def test_build_issue_payload_deduplicates_fingerprints(self) -> None:
        payload = github_issues.build_issue_payload(
            "Runtime failure",
            "Stack trace",
            labels=["bug", "bug", "runtime"],
            fingerprint="critical-path",
        )

        self.assertIsNotNone(payload)
        self.assertEqual(payload["labels"], ["bug", "runtime"])
        self.assertIn("<!-- fingerprint:", payload["body"])

        digest = hashlib.sha256("critical-path".encode("utf-8")).hexdigest()
        marker_path = Path(self.tempdir.name) / digest
        self.assertTrue(marker_path.exists())

        duplicate = github_issues.build_issue_payload(
            "Runtime failure",
            "Stack trace",
            labels=["bug"],
            fingerprint="critical-path",
        )

        self.assertIsNone(duplicate)


class CreateIssueTests(TestCase):
    def test_http_errors_raise_and_log(self) -> None:
        response = mock.Mock()
        response.status_code = 500
        response.text = "boom"
        response.raise_for_status.side_effect = requests.HTTPError("boom")

        with mock.patch("core.github_issues.resolve_repository", return_value=("owner", "repo")):
            with mock.patch("core.github_issues.get_github_token", return_value="token"):
                with mock.patch("requests.post", return_value=response) as post:
                    with self.assertLogs("core.github_issues", level="ERROR") as logs:
                        with self.assertRaises(requests.HTTPError):
                            github_issues.create_issue("Title", "Body")

        post.assert_called_once()
        self.assertIn("GitHub issue creation failed", "".join(logs.output))


class ReportRuntimeIssueTaskTests(TestCase):
    def test_task_reports_successfully(self) -> None:
        response = mock.Mock()
        response.status_code = 201

        with mock.patch("core.github_issues.resolve_repository", return_value=("owner", "repo")):
            with mock.patch("core.github_issues.get_github_token", return_value="token"):
                with mock.patch("requests.post", return_value=response) as post:
                    with self.assertLogs("core.tasks", level="INFO") as logs:
                        result = report_runtime_issue("Title", "Body", labels=["bug"])

        self.assertIs(result, response)
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://api.github.com/repos/owner/repo/issues")
        self.assertEqual(kwargs["timeout"], github_issues.REQUEST_TIMEOUT)
        self.assertTrue(any("Reported runtime issue" in message for message in logs.output))

    def test_task_logs_and_raises_on_failure(self) -> None:
        response = mock.Mock()
        response.status_code = 500
        response.text = "failure"
        response.raise_for_status.side_effect = requests.HTTPError("failure")

        with mock.patch("core.github_issues.resolve_repository", return_value=("owner", "repo")):
            with mock.patch("core.github_issues.get_github_token", return_value="token"):
                with mock.patch("requests.post", return_value=response):
                    with self.assertLogs("core.tasks", level="ERROR") as logs:
                        with self.assertRaises(requests.HTTPError):
                            report_runtime_issue("Title", "Body")

        self.assertTrue(any("Failed to report runtime issue" in message for message in logs.output))
