# Arthexis Django Project

This repository contains a basic [Django](https://www.djangoproject.com/) project.

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Apply database migrations:
   ```bash
   python manage.py migrate
   ```
3. Run the development server:
   ```bash
   python manage.py runserver
   ```

   To have the server automatically restart when files change, set
   the `DJANGO_DEV_RELOAD` environment variable:

   ```bash
   DJANGO_DEV_RELOAD=1 python manage.py runserver
   ```

The default configuration uses SQLite and is for local development only.
To use PostgreSQL instead, set the `POSTGRES_DB` environment variable (and
optionally `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST` and
`POSTGRES_PORT`) before running management commands. If `POSTGRES_DB` is
defined, the project will connect to a PostgreSQL server using these
settings.

## VS Code

Launch configurations are provided in `.vscode/launch.json`:

1. **Run Django Server** – starts the site normally without the debugger.
2. **Debug Django Server** – runs the server with debugging enabled.

Open the *Run and Debug* pane in VS Code and choose the desired configuration.

## Internationalization

English is the primary language for this project.  A Spanish translation is
available under `locale/es`.  You can activate it by setting the `LANGUAGE_CODE`
setting or selecting the language via Django's i18n mechanisms.  The supported
languages are defined in `config/settings.py`.

## Maintaining Documentation

Documentation is split across multiple files. `README.base.md` provides the
overview while each app has its own `README.md` with app-specific details.
After updating any of these files, regenerate `README.md` with:

```bash
python manage.py build_readme
```

Avoid editing the combined `README.md` directly.

## Subdomain Routing

The project uses Django's **sites** framework together with the `website`
app to select which application handles requests for a given domain.  Each
`Site`'s *name* should be set to the label of the Django app whose `urls`
module will serve that domain.  Requests for unknown domains fall back to
the `readme` site which renders this documentation.
