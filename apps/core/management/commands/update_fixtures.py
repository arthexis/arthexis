from __future__ import annotations

import inspect
import json
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core import serializers
from django.core.management.base import BaseCommand

from apps.core.fixtures import ensure_seed_data_flags


class Command(BaseCommand):
    """Persist database changes back to fixture files."""

    help = "Update fixture files from current database state"

    def handle(self, *args, **options):
        base = Path(settings.BASE_DIR)
        for path in sorted(base.glob("**/fixtures/*.json")):
            if path.name.startswith("users__"):
                continue
            try:
                with path.open() as fh:
                    data = json.load(fh)
            except Exception:
                continue
            if not isinstance(data, list):
                continue
            use_natural = all(isinstance(obj, dict) and "pk" not in obj for obj in data)
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
                instance = None
                if "pk" in obj:
                    instance = model.objects.filter(pk=obj["pk"]).first()
                else:
                    manager = model._default_manager
                    get_natural = getattr(manager, "get_by_natural_key", None)
                    if get_natural:
                        sig = inspect.signature(get_natural)
                        params = [p.name for p in list(sig.parameters.values())[1:]]
                        try:
                            args = [obj.get("fields", {}).get(p) for p in params]
                        except Exception:
                            args = []
                        if None not in args:
                            try:
                                instance = get_natural(*args)
                            except Exception:
                                instance = None
                if instance is not None:
                    instances.append(instance)
            if instances:
                content = serializers.serialize(
                    "json",
                    instances,
                    indent=2,
                    use_natural_foreign_keys=use_natural,
                    use_natural_primary_keys=use_natural,
                )
            else:
                content = "[]"

            content = ensure_seed_data_flags(content)
            path.write_text(content)
