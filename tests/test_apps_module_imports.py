"""Critical regression test for importability of modules under ``apps``."""

from __future__ import annotations

import importlib
import pkgutil

import apps
import pytest

pytestmark = [pytest.mark.critical]

# These modules are intentionally skipped because they depend on optional
# platform/runtime services or have import-time side effects that are not
# available in the test environment.
MODULE_IMPORT_DENYLIST = {
    "apps.cards.always_on",
    "apps.embeds.routes",
    "apps.embeds.urls",
    "apps.embeds.views",
    "apps.ftp.authorizers",
    "apps.ftp.management.commands.runftpserver",
    "apps.ocpp.admin.certificates",
    "apps.ocpp.admin.cp_firmware",
    "apps.ocpp.coverage_stubs",
}


def _iter_non_test_apps_modules() -> list[str]:
    """Return all importable ``apps.*`` module names excluding test modules."""

    modules: list[str] = []
    for module_info in pkgutil.walk_packages(apps.__path__, prefix="apps."):
        module_name = module_info.name
        if ".tests." in module_name or module_name.endswith(".tests"):
            continue
        modules.append(module_name)
    return modules


def test_apps_modules_import_cleanly() -> None:
    """Import non-test ``apps.*`` modules and report failures with details."""

    failures: list[str] = []

    for module_name in _iter_non_test_apps_modules():
        if module_name in MODULE_IMPORT_DENYLIST:
            continue

        try:
            importlib.import_module(module_name)
        except Exception as exc:  # pragma: no cover - assertion reports errors
            failures.append(f"{module_name}: {type(exc).__name__}: {exc}")

    assert not failures, "\n".join(failures)
