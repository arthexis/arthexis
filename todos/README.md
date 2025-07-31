# Todos

Simple app for tracking tasks within the project.

## API

- `GET /todos/` – list all todos.
- `POST /todos/` – create a new todo. JSON body: `{ "text": "Buy milk" }`
- `POST /todos/<id>/toggle/` – toggle completion status.

## Importing from Code

Run the management command to create todo items from `# TODO` comments found in
project files:

```bash
python manage.py import_todos
```
