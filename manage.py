#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def main():
    """Run administrative tasks."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    try:
        from django.core.management import execute_from_command_line
        from django.conf import settings
        from django.utils.autoreload import run_with_reloader
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc

    def _execute():
        execute_from_command_line(sys.argv)

    if (
        os.environ.get("DJANGO_DEV_RELOAD")
        and settings.DEBUG
        and os.environ.get("RUN_MAIN") != "true"
    ):
        run_with_reloader(_execute)
    else:
        _execute()


if __name__ == "__main__":
    main()
