# Django process bootstrap

Arthexis requires environment loading and SQLite driver selection before Django assembles settings.

`config.bootstrap.bootstrap_django_environment()` owns that shared process bootstrap. The assembled `config.settings` package calls it before importing any settings modules, so Django-aware entrypoints that initialize through `DJANGO_SETTINGS_MODULE=config.settings` receive the same prerequisites as `manage.py`, ASGI, and WSGI.

The helper is idempotent within a process. Existing entrypoints may continue to bootstrap explicitly while migration toward the shared helper is completed.
