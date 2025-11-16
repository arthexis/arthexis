from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from core.models import Todo


class TodoCreatedOnNormalizationTests(TestCase):
    def test_save_clamps_future_created_on(self):
        moment = timezone.now()
        future = moment + timedelta(days=3)

        with patch("django.utils.timezone.now", return_value=moment):
            todo = Todo.objects.create(request="Task", created_on=future)

        self.assertEqual(todo.created_on, moment)

    def test_refresh_active_normalizes_existing_future_timestamp(self):
        base_time = timezone.now()
        future_time = base_time + timedelta(days=2)

        todo = Todo.objects.create(request="Task")
        Todo.all_objects.filter(pk=todo.pk).update(created_on=future_time)
        todo.refresh_from_db()
        self.assertEqual(todo.created_on, future_time)

        with patch("django.utils.timezone.now", return_value=base_time):
            todos = Todo.refresh_active(now=base_time)

        todo.refresh_from_db()
        self.assertEqual(todo.created_on, base_time)
        self.assertIn(todo, todos)
