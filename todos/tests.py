import json
import os
import tempfile

from django.test import Client, TestCase

from .models import Todo
from .utils import create_todos_from_comments


class TodoAPITests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_create_and_toggle(self):
        resp = self.client.post(
            '/todos/',
            data=json.dumps({'text': 'write docs'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201)
        todo_id = resp.json()['id']
        resp = self.client.post(f'/todos/{todo_id}/toggle/')
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
