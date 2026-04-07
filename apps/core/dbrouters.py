"""Database router for external app SQLite databases."""

from django.conf import settings

from config.settings.external_dbs import external_app_database_alias_mapping


class ExternalAppDatabaseRouter:
    """Route external-app models to dedicated SQLite databases in ``work/dbs``."""

    _external_app_aliases: tuple[str, ...] = ()
    _module_alias_mapping: dict[str, str] = {}

    @classmethod
    def _get_module_alias_mapping(cls) -> dict[str, str]:
        """Return and cache module-root to alias mapping for configured plugins."""

        external_apps = tuple(getattr(settings, "ARTHEXIS_EXTERNAL_APPS", []))
        if external_apps != cls._external_app_aliases:
            cls._external_app_aliases = external_apps
            cls._module_alias_mapping = external_app_database_alias_mapping(
                list(external_apps)
            )
        return cls._module_alias_mapping

    def _external_alias_for_model(self, model) -> str | None:
        """Return the plugin database alias for a model, if it is external."""

        module_name = getattr(model, "__module__", "")
        for module_root, alias in self._get_module_alias_mapping().items():
            if module_name == module_root or module_name.startswith(f"{module_root}."):
                return alias
        return None

    def db_for_read(self, model, **hints):
        """Route read operations for external models to their plugin database."""

        del hints
        return self._external_alias_for_model(model)

    def db_for_write(self, model, **hints):
        """Route write operations for external models to their plugin database."""

        del hints
        return self._external_alias_for_model(model)

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        """Allow migrations only on each model's assigned database."""

        del app_label, model_name

        module_alias_mapping = self._get_module_alias_mapping()
        external_aliases = set(module_alias_mapping.values())

        if db in external_aliases and hints.get("model") is None:
            return False

        model = hints.get("model")
        if model is None:
            return None

        external_alias = self._external_alias_for_model(model)
        if external_alias is None:
            if db in external_aliases:
                return False
            return None

        if db == external_alias:
            return True

        if db == "default":
            return False

        return None
