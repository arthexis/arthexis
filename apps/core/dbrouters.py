"""Database router for external app SQLite databases."""

from django.conf import settings

from config.settings.external_dbs import external_app_database_alias, external_app_module


class ExternalAppDatabaseRouter:
    """Route external-app models to dedicated SQLite databases in ``work/dbs``."""

    def _external_alias_for_model(self, model) -> str | None:
        module_name = getattr(model, "__module__", "")
        for index, app_path in enumerate(
            list(getattr(settings, "ARTHEXIS_EXTERNAL_APPS", [])),
            start=1,
        ):
            module_root = external_app_module(app_path)
            if module_name == module_root or module_name.startswith(f"{module_root}."):
                return external_app_database_alias(app_path, fallback_index=index)
        return None

    def db_for_read(self, model, **hints):
        return self._external_alias_for_model(model)

    def db_for_write(self, model, **hints):
        return self._external_alias_for_model(model)

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        del app_label, model_name

        model = hints.get("model")
        if model is None:
            return None

        external_alias = self._external_alias_for_model(model)
        if external_alias is None:
            return None

        if db == external_alias:
            return True

        if db == "default":
            return False

        return None
