import json
import os
import tempfile

from django.test import SimpleTestCase, TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from unittest.mock import patch
from types import SimpleNamespace

from . import Credentials, DEFAULT_PACKAGE
from .models import PackageConfig, TestLog, Todo
from .utils import create_todos_from_comments
from . import utils


class CredentialsTests(SimpleTestCase):
    def test_token_args(self):
        c = Credentials(token="abc")
        self.assertEqual(c.twine_args(), ["--username", "__token__", "--password", "abc"])

    def test_userpass_args(self):
        c = Credentials(username="u", password="p")
        self.assertEqual(c.twine_args(), ["--username", "u", "--password", "p"])

    def test_missing(self):
        c = Credentials()
        with self.assertRaises(ValueError):
            c.twine_args()


class PackageTests(SimpleTestCase):
    def test_default_name(self):
        self.assertEqual(DEFAULT_PACKAGE.name, "arthexis")


class PackageConfigTests(TestCase):
    def test_to_package_and_credentials(self):
        cfg = PackageConfig.objects.create(
            name="pkg",
            description="desc",
            author="me",
            email="me@example.com",
            python_requires=">=3.10",
            license="MIT",
        )
        package = cfg.to_package()
        self.assertEqual(package.name, "pkg")
        self.assertIsNone(cfg.to_credentials())


class PackageAdminTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="release_admin", password="pass", email="a@a.com"
        )
        self.client = Client()
        self.client.force_login(self.admin)
        self.cfg = PackageConfig.objects.create(
            name="pkg",
            description="desc",
            author="me",
            email="me@example.com",
            python_requires=">=3.10",
            license="MIT",
            token="tok",
        )

    def test_build_action_calls_utils(self):
        url = reverse("admin:release_packageconfig_changelist")
        with patch("release.admin.utils.build") as mock_build:
            response = self.client.post(
                url,
                {"action": "build_package", "_selected_action": [self.cfg.pk]},
                follow=True,
            )
            self.assertEqual(response.status_code, 200)
            mock_build.assert_called_once()


class TestLogTests(TestCase):
    @patch("release.utils.subprocess.run")
    def test_run_tests_success(self, mock_run):
        mock_run.return_value = SimpleNamespace(returncode=0, stdout="ok", stderr="")
        log = utils.run_tests()
        self.assertEqual(log.status, "success")
        self.assertIn("ok", log.output)

    @patch("release.utils.subprocess.run")
    def test_run_tests_failure(self, mock_run):
        mock_run.return_value = SimpleNamespace(returncode=1, stdout="", stderr="bad")
        log = utils.run_tests()
        self.assertEqual(log.status, "failure")
        self.assertIn("bad", log.output)


class TestLogAdminTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="release_admin", password="pass", email="a@a.com"
        )
        self.client = Client()
        self.client.force_login(self.admin)
        self.log = TestLog.objects.create(status="success", output="x")

    def test_purge_action_deletes_logs(self):
        url = reverse("admin:release_testlog_changelist")
        response = self.client.post(
            url,
            {"action": "purge_logs", "_selected_action": [self.log.pk]},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(TestLog.objects.count(), 0)


class TodoAPITests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_create_and_toggle(self):
        resp = self.client.post(
            '/release/todos/',
            data=json.dumps({'text': 'write docs'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201)
        todo_id = resp.json()['id']
        resp = self.client.post(f'/release/todos/{todo_id}/toggle/')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['completed'])


class TodoImportTests(TestCase):
    def test_create_from_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            sample = os.path.join(tmp, 'sample.py')
            with open(sample, 'w') as fh:
                fh.write('# TODO: add tests\nprint(1)\n')
            create_todos_from_comments(base_path=tmp)
            todo = Todo.objects.get()
            self.assertEqual(todo.text, 'add tests')
            self.assertEqual(todo.file_path, 'sample.py')
            self.assertEqual(todo.line_number, 1)
            create_todos_from_comments(base_path=tmp)
            self.assertEqual(Todo.objects.count(), 1)
