"""Utilities to restore default navigation modules."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass


ModuleDefinition = Mapping[str, object]
LandingDefinition = tuple[str, str]


ROLE_MODULE_DEFAULTS: Mapping[str, tuple[ModuleDefinition, ...]] = {
    "Watchtower": (
        {
            "application": "ocpp",
            "path": "/ocpp/",
            "menu": "Chargers",
            "landings": (
                ("/ocpp/cpms/dashboard/", "CPMS Online Dashboard"),
                ("/ocpp/evcs/simulator/", "Charge Point Simulator"),
                ("/ocpp/rfid/validator/", "RFID Tag Validator"),
            ),
        },
        {
            "application": "awg",
            "path": "/awg/",
            "menu": "",
            "landings": (
                ("/awg/", "AWG Cable Calculator"),
                ("/awg/energy-tariff/", "Energy Tariff Calculator"),
            ),
        },
    ),
}


@dataclass
class ReloadSummary:
    """Report about the changes performed while restoring defaults."""

    roles_processed: int = 0
    modules_created: int = 0
    modules_updated: int = 0
    landings_created: int = 0
    landings_updated: int = 0

    @property
    def has_changes(self) -> bool:
        return any(
            (
                self.modules_created,
                self.modules_updated,
                self.landings_created,
                self.landings_updated,
            )
        )


def _manager(model, name: str):
    manager = getattr(model, name, None)
    if manager is not None:
        return manager
    return model.objects


def reload_default_modules(Application, Module, Landing, NodeRole) -> ReloadSummary:
    """Ensure default navigation modules exist for the configured roles."""

    summary = ReloadSummary()
    application_manager = _manager(Application, "all_objects")
    module_manager = _manager(Module, "all_objects")
    landing_manager = _manager(Landing, "all_objects")
    role_manager = _manager(NodeRole, "all_objects")

    for role_name, module_definitions in ROLE_MODULE_DEFAULTS.items():
        try:
            role = role_manager.get(name=role_name)
        except NodeRole.DoesNotExist:
            continue

        summary.roles_processed += 1

        for definition in module_definitions:
            app_name: str = definition["application"]  # type: ignore[assignment]
            try:
                application = application_manager.get(name=app_name)
            except Application.DoesNotExist:
                continue

            module, created = module_manager.get_or_create(
                node_role=role,
                path=definition["path"],
                defaults={
                    "application": application,
                    "menu": definition["menu"],
                    "is_seed_data": True,
                    "is_deleted": False,
                },
            )
            if created:
                summary.modules_created += 1

            module_updates: list[str] = []
            if module.application_id != application.id:
                module.application = application
                module_updates.append("application")
            if module.menu != definition["menu"]:
                module.menu = definition["menu"]  # type: ignore[index]
                module_updates.append("menu")
            if getattr(module, "is_deleted", False):
                module.is_deleted = False
                module_updates.append("is_deleted")
            if not getattr(module, "is_seed_data", False):
                module.is_seed_data = True
                module_updates.append("is_seed_data")
            if module_updates:
                module.save(update_fields=module_updates)
                if not created:
                    summary.modules_updated += 1

            landings: Iterable[tuple[str, str]] = definition["landings"]  # type: ignore[index]
            for path, label in landings:
                landing, landing_created = landing_manager.get_or_create(
                    module=module,
                    path=path,
                    defaults={
                        "label": label,
                        "description": "",
                        "enabled": True,
                    },
                )
                if landing_created:
                    summary.landings_created += 1

                landing_updates: list[str] = []
                if landing.label != label:
                    landing.label = label
                    landing_updates.append("label")
                if landing.description:
                    landing.description = ""
                    landing_updates.append("description")
                if not landing.enabled:
                    landing.enabled = True
                    landing_updates.append("enabled")
                if getattr(landing, "is_deleted", False):
                    landing.is_deleted = False
                    landing_updates.append("is_deleted")
                if not getattr(landing, "is_seed_data", False):
                    landing.is_seed_data = True
                    landing_updates.append("is_seed_data")
                if landing_updates:
                    landing.save(update_fields=landing_updates)
                    if not landing_created:
                        summary.landings_updated += 1

    return summary
