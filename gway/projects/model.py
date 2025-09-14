"""Utilities for interacting with Django models via gway."""

from __future__ import annotations

import importlib
import importlib.util
import os
import pathlib
import sys


def _ensure_setup() -> None:
    """Ensure Django is configured.

    If ``DJANGO_SETTINGS_MODULE`` is unset, attempt to derive it from
    ``MODEL_SOURCE`` which should point to a ``settings.py`` file.
    """
    settings_mod = os.environ.get("DJANGO_SETTINGS_MODULE")
    if not settings_mod:
        model_source = os.environ.get("MODEL_SOURCE")
        if model_source:
            settings_path = pathlib.Path(model_source).resolve()
            module_name = ".".join(settings_path.with_suffix("").parts[-2:])
            spec = importlib.util.spec_from_file_location(module_name, settings_path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            assert spec.loader is not None
            spec.loader.exec_module(module)
            os.environ["DJANGO_SETTINGS_MODULE"] = module_name
        else:
            raise ModuleNotFoundError(
                "MODEL_SOURCE not set and DJANGO_SETTINGS_MODULE missing"
            )
    else:
        importlib.import_module(settings_mod)


def __getattr__(name: str):
    _ensure_setup()
    from django.apps import apps

    try:
        return apps.get_model(name)
    except LookupError as exc:  # pragma: no cover - passthrough for missing models
        raise AttributeError(name) from exc
