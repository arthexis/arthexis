"""Critical regression coverage for model import integrity across installed apps."""

from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType

import pytest
from django.conf import settings

pytestmark = [pytest.mark.critical, pytest.mark.regression]


def _import_models_module_if_present(app_label: str) -> ModuleType | None:
    """Import and return an app's models module when one is defined."""

    models_module_name = f"{app_label}.models"
    if importlib.util.find_spec(models_module_name) is None:
        return None

    return importlib.import_module(models_module_name)


def _iter_models_submodule_names(models_module: ModuleType) -> list[str]:
    """Collect dotted names for importable submodules under a models package."""

    module_path_locations = getattr(models_module, "__path__", None)
    if not module_path_locations:
        return []

    return [
        module_info.name
        for module_info in pkgutil.walk_packages(
            path=module_path_locations,
            prefix=f"{models_module.__name__}.",
        )
    ]


def test_all_installed_app_models_modules_import_cleanly() -> None:
    """Every installed local app with models must import without configuration errors."""

    failures: list[str] = []

    for app_label in settings.LOCAL_APPS:
        models_module = _import_models_module_if_present(app_label)
        if models_module is None:
            continue

        for submodule_name in _iter_models_submodule_names(models_module):
            try:
                importlib.import_module(submodule_name)
            except RuntimeError as exc:
                failures.append(f"{submodule_name}: {exc}")

    assert not failures, "\n".join(failures)
