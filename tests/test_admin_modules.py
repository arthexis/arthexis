from __future__ import annotations

import importlib

from django.conf import settings


def _admin_module_names() -> list[str]:
    return [f"{app_path}.admin" for app_path in settings.LOCAL_APPS]


def test_local_apps_define_importable_admin_modules():
    missing_modules: list[str] = []
    import_errors: list[tuple[str, Exception]] = []

    for module_name in _admin_module_names():
        spec = importlib.util.find_spec(module_name)
        if spec is None:
            missing_modules.append(module_name)
            continue

        try:
            importlib.import_module(module_name)
        except Exception as exc:  # pragma: no cover - failure case captured in assertion
            import_errors.append((module_name, exc))

    assert not missing_modules, f"Missing admin modules: {', '.join(missing_modules)}"
    assert not import_errors, \
        "Admin modules raised errors: " + \
        "; ".join(f"{name}: {exc}" for name, exc in import_errors)
