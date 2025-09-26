import contextlib
import importlib
import os
import sys

import manage


def test_manage_runserver_enables_debug(monkeypatch):
    """`runserver` should enable Django's debug mode by default."""

    monkeypatch.delenv("DEBUG", raising=False)
    monkeypatch.setattr(
        "django.core.management.execute_from_command_line", lambda argv: None
    )
    monkeypatch.setattr(sys, "argv", ["manage.py", "runserver"])

    try:
        manage.main()
    finally:
        importlib.reload(
            importlib.import_module("django.core.management.commands.runserver")
        )
        with contextlib.suppress(ModuleNotFoundError):
            importlib.reload(
                importlib.import_module(
                    "django.contrib.staticfiles.management.commands.runserver"
                )
            )

    assert os.environ["DEBUG"] == "1"
