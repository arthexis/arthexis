"""Ownership regressions for the application registry domain."""

from apps.app.checks import apps_registry as owned_checks
from apps.app.management.commands import apps as owned_apps_command
from apps.app.management.commands import enabled_apps_lock as owned_lock_command
from apps.core.checks import apps_registry as legacy_checks
from apps.core.management.commands import apps as legacy_apps_command
from apps.core.management.commands import enabled_apps_lock as legacy_lock_command


def test_core_check_module_resolves_to_app_owned_implementation():
    assert (
        legacy_checks.get_apps_registry_configuration_errors
        is owned_checks.get_apps_registry_configuration_errors
    )
    assert (
        legacy_checks.enforce_apps_registry_configuration
        is owned_checks.enforce_apps_registry_configuration
    )


def test_system_check_ids_remain_compatible():
    assert owned_checks.APPS_REGISTRY_ENTRY_NOT_IMPORTABLE_ID == "core.E001"
    assert owned_checks.APPS_REGISTRY_UNLISTED_LOCAL_APP_ID == "core.E002"
    assert owned_checks.EXTERNAL_APP_PATH_INVALID_ID == "core.E003"


def test_core_command_modules_are_compatibility_aliases():
    assert legacy_apps_command.Command is owned_apps_command.Command
    assert legacy_lock_command.Command is owned_lock_command.Command
