from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

from django.apps import apps
from django.conf import settings
from django.core import serializers
from django.core.exceptions import ObjectDoesNotExist
from django.core.management.base import BaseCommand
from parler.models import TranslatableModel

from apps.core.fixtures import ensure_seed_data_flags


class Command(BaseCommand):
    """Persist database changes back to fixture files."""

    help = "Update fixture files from current database state"

    def _load_fixture_data(self, path: Path) -> tuple[list[dict[str, Any]], bool] | None:
        """Load fixture JSON and return object list plus natural-key mode flag."""

        try:
            with path.open(encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            self.stderr.write(self.style.WARNING(f"Could not process fixture {path}: {exc}"))
            return None

        if not isinstance(data, list):
            return None

        use_natural = all(isinstance(obj, dict) and "pk" not in obj for obj in data)
        return data, use_natural

    def _supports_natural_key(self, model) -> bool:
        """Return whether a model supports natural key serialization and lookup."""

        natural_key = getattr(model, "natural_key", None)
        get_natural = getattr(model._default_manager, "get_by_natural_key", None)
        return callable(natural_key) and callable(get_natural)

    def _collect_translations(self, instance, use_natural: bool):
        """Collect Parler translation rows for a translatable model instance."""

        if not isinstance(instance, TranslatableModel):
            return []

        translations = []
        for translations_model in instance._parler_meta.get_all_models():
            if use_natural and not self._supports_natural_key(translations_model):
                continue
            translations.extend(translations_model.objects.filter(master=instance))
        return translations

    def _resolve_instance(self, model, obj: dict[str, Any], use_natural: bool):
        """Resolve one fixture object to a current DB instance by pk or natural key."""

        if "pk" in obj:
            return model.objects.filter(pk=obj["pk"]).first()

        if not use_natural:
            return None

        manager = model._default_manager
        get_natural = getattr(manager, "get_by_natural_key", None)
        if not get_natural:
            return None

        sig = inspect.signature(get_natural)
        params = [p.name for p in list(sig.parameters.values())[1:]]
        natural_key_args = [obj.get("fields", {}).get(param) for param in params]
        if None in natural_key_args:
            return None

        try:
            return get_natural(*natural_key_args)
        except ObjectDoesNotExist:
            return None

    def _serialize_instances(self, instances, use_natural: bool) -> str:
        """Serialize deduplicated instances and linked translations to JSON text."""

        if not instances:
            return "[]"

        serialized_instances = []
        seen = set()
        for instance in instances:
            key = (instance._meta.label_lower, instance.pk)
            if key not in seen:
                seen.add(key)
                serialized_instances.append(instance)

            for translation in self._collect_translations(instance, use_natural):
                translation_key = (translation._meta.label_lower, translation.pk)
                if translation_key not in seen:
                    seen.add(translation_key)
                    serialized_instances.append(translation)

        return serializers.serialize(
            "json",
            serialized_instances,
            indent=2,
            use_natural_foreign_keys=use_natural,
            use_natural_primary_keys=use_natural,
        )

    def handle(self, *args, **options):
        """Update each fixture file with current DB-backed records for included models."""

        base = Path(settings.BASE_DIR)
        for path in sorted(base.glob("**/fixtures/*.json")):
            if path.name.startswith("users__"):
                continue

            loaded = self._load_fixture_data(path)
            if loaded is None:
                continue
            data, use_natural = loaded

            instances = []
            for obj in data:
                if not isinstance(obj, dict):
                    continue

                model_label = obj.get("model")
                if not model_label:
                    continue

                try:
                    model = apps.get_model(model_label)
                except LookupError:
                    continue

                instance = self._resolve_instance(model, obj, use_natural)
                if instance is not None:
                    instances.append(instance)

            try:
                content = self._serialize_instances(instances, use_natural)
                content = ensure_seed_data_flags(content)
                path.write_text(content, encoding="utf-8")
            except Exception as exc:
                self.stderr.write(self.style.WARNING(f"Failed to update fixture {path}: {exc}"))
                continue
