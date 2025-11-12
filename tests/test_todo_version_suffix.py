import os
import sys
from pathlib import Path
from unittest import mock

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.test import SimpleTestCase, TestCase

from core.models import Todo


class TodoVersionLabelParsingTests(SimpleTestCase):
    def test_accepts_trailing_plus_suffix(self):
        parsed = Todo._parse_version_label("0.1.31+")
        self.assertIsNotNone(parsed)
        self.assertEqual(str(parsed), "0.1.31")

    def test_preserves_local_version_metadata(self):
        parsed = Todo._parse_version_label("0.1.31+build")
        self.assertIsNotNone(parsed)
        self.assertEqual(str(parsed), "0.1.31+build")

    @mock.patch.object(Todo, "release_timeline", return_value=[])
    @mock.patch.object(Todo, "_default_version", return_value="0.1.31+")
    def test_version_context_parses_trailing_plus(
        self, _default_version, _release_timeline
    ):
        label, parsed, timeline = Todo.version_context()
        self.assertEqual(label, "0.1.31+")
        self.assertEqual(timeline, [])
        self.assertIsNotNone(parsed)
        self.assertEqual(str(parsed), "0.1.31")


class TodoRefreshActiveVersionSuffixTests(TestCase):
    @mock.patch.object(Todo, "release_timeline", return_value=[])
    @mock.patch.object(Todo, "_default_version", return_value="0.1.31")
    def test_refresh_active_accepts_todo_with_trailing_plus(
        self, _default_version, _release_timeline
    ):
        todo = Todo.objects.create(request="Task", version="0.1.31+")
        active = Todo.refresh_active()
        self.assertIn(todo.pk, [item.pk for item in active])
        todo.refresh_from_db()
        self.assertIsNone(todo.stale_on)
        self.assertEqual(todo.version, "0.1.31+")

    @mock.patch.object(Todo, "release_timeline", return_value=[])
    @mock.patch.object(Todo, "_default_version", return_value="0.1.31+")
    def test_refresh_active_accepts_current_version_with_trailing_plus(
        self, _default_version, _release_timeline
    ):
        todo = Todo.objects.create(request="Task", version="0.1.31")
        active = Todo.refresh_active()
        self.assertIn(todo.pk, [item.pk for item in active])
        todo.refresh_from_db()
        self.assertIsNone(todo.stale_on)
