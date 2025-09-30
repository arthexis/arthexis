"""Database router that directs selected models to named aliases."""

from __future__ import annotations

from typing import Any

from django.conf import settings


class ModelDatabaseRouter:
    """Route models to database aliases configured in ``settings.MODEL_DATABASES``."""

    default_alias = "default"

    @staticmethod
    def _alias_for_model(model: type[Any]) -> str:
        label = getattr(model._meta, "label_lower", None)
        if not label:
            return ModelDatabaseRouter.default_alias
        alias = settings.MODEL_DATABASES.get(label)
        if alias and alias in settings.DATABASES:
            return alias
        return ModelDatabaseRouter.default_alias

    def db_for_read(self, model: type[Any], **hints: Any) -> str | None:
        return self._alias_for_model(model)

    def db_for_write(self, model: type[Any], **hints: Any) -> str | None:
        return self._alias_for_model(model)

    def allow_relation(self, obj1: Any, obj2: Any, **hints: Any) -> bool | None:
        return self._alias_for_model(obj1.__class__) == self._alias_for_model(obj2.__class__)

    def allow_migrate(
        self,
        db: str,
        app_label: str,
        model_name: str | None = None,
        **hints: Any,
    ) -> bool | None:
        if not model_name:
            return None
        label = f"{app_label}.{model_name}".lower()
        alias = settings.MODEL_DATABASES.get(label)
        if alias and alias in settings.DATABASES:
            return db == alias
        if alias and alias not in settings.DATABASES:
            return db == self.default_alias
        return db == self.default_alias
