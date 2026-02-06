from __future__ import annotations

import json
import logging
import tempfile
from functools import lru_cache
from pathlib import Path
from zipfile import ZipFile

from django.apps import apps
from django.conf import settings
from django.core.management import call_command

from .fixtures import _fixture_entry_targets_installed_apps

logger = logging.getLogger(__name__)

_SEED_FIXTURE_IGNORED_FIELDS = {"is_seed_data", "is_deleted", "is_user_data"}


@lru_cache(maxsize=1)
def _seed_fixture_index():
    base = Path(settings.BASE_DIR)
    index: dict[str, dict[str, object]] = {}
    for path in base.glob("**/fixtures/*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, list) or not data:
            continue
        obj = data[0]
        if not isinstance(obj, dict):
            continue
        label = obj.get("model")
        if not isinstance(label, str):
            continue
        fields = obj.get("fields") or {}
        if not isinstance(fields, dict):
            fields = {}
        comparable_fields = {
            key: value
            for key, value in fields.items()
            if key not in _SEED_FIXTURE_IGNORED_FIELDS
        }
        pk = obj.get("pk")
        entries = index.setdefault(label, {"pk": {}, "fields": []})
        pk_index = entries.setdefault("pk", {})
        field_index = entries.setdefault("fields", [])
        if pk is not None:
            pk_index[pk] = path
        if comparable_fields:
            field_index.append((comparable_fields, path))
    return index


def _seed_fixture_path(instance, *, index=None) -> Path | None:
    label = f"{instance._meta.app_label}.{instance._meta.model_name}"
    fixture_index = index or _seed_fixture_index()
    entries = fixture_index.get(label)
    if not entries:
        return None
    pk = getattr(instance, "pk", None)
    pk_index = entries.get("pk", {})
    if pk is not None:
        path = pk_index.get(pk)
        if path is not None:
            return path
    for comparable_fields, path in entries.get("fields", []):
        match = True
        if not isinstance(comparable_fields, dict):
            continue
        for field_name, value in comparable_fields.items():
            if not hasattr(instance, field_name):
                match = False
                break
            if getattr(instance, field_name) != value:
                match = False
                break
        if match:
            return path
    return None


def _seed_zip_dir() -> Path:
    path = Path(settings.BASE_DIR) / "config" / "seeds"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _seed_zip_paths() -> list[Path]:
    return sorted(_seed_zip_dir().glob("*.zip"))


def _seed_fixture_name(model) -> str:
    opts = model._meta
    return f"{opts.app_label}__{opts.model_name}__local_seed.json"


def _seed_fixture_text_from_bytes(content: bytes) -> str | None:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("latin-1")


def _seed_fixture_entries_from_text(text: str) -> list[dict]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [obj for obj in data if isinstance(obj, dict)]


def _seed_fixture_entries_from_bytes(content: bytes) -> list[dict]:
    text = _seed_fixture_text_from_bytes(content)
    if text is None:
        return []
    return _seed_fixture_entries_from_text(text)


def _seed_fixture_has_unapplied_entries(entries: list[dict]) -> bool:
    pks_by_model: dict[type, list] = {}
    for obj in entries:
        if not _fixture_entry_targets_installed_apps(obj):
            continue
        label = obj.get("model")
        pk = obj.get("pk")
        if not label:
            continue
        if pk is None:
            return True
        try:
            model = apps.get_model(label)
        except LookupError:
            continue
        pks_by_model.setdefault(model, []).append(pk)

    for model, pks in pks_by_model.items():
        try:
            unique_pks = set(pks)
        except TypeError:
            return True
        manager = getattr(model, "all_objects", model._default_manager)
        try:
            existing = manager.filter(pk__in=unique_pks).count()
        except (ValueError, TypeError):
            return True
        if existing < len(unique_pks):
            return True
    return False


def load_local_seed_zips(*, verbosity: int = 0, only_paths: list[Path] | None = None) -> int:
    from apps.core.fixtures import ensure_seed_data_flags

    loaded = 0
    paths = _seed_zip_paths() if only_paths is None else only_paths
    for zip_path in paths:
        try:
            with ZipFile(zip_path) as zf:
                for name in zf.namelist():
                    if not name.endswith(".json"):
                        continue
                    content_bytes = zf.read(name)
                    text = _seed_fixture_text_from_bytes(content_bytes)
                    if text is None:
                        continue
                    entries = _seed_fixture_entries_from_text(text)
                    if not entries:
                        continue
                    if not _seed_fixture_has_unapplied_entries(entries):
                        continue
                    text = ensure_seed_data_flags(text)
                    with tempfile.NamedTemporaryFile(
                        mode="w",
                        suffix=".json",
                        delete=False,
                        encoding="utf-8",
                    ) as temp_file:
                        temp_file.write(text)
                        temp_path = Path(temp_file.name)
                    try:
                        call_command(
                            "loaddata",
                            str(temp_path),
                            ignorenonexistent=True,
                            verbosity=verbosity,
                        )
                        loaded += 1
                    finally:
                        temp_path.unlink(missing_ok=True)
        except Exception:
            logger.exception("Unable to load local seed data from %s", zip_path)
    return loaded


def _seed_datum_is_default(instance, *, index=None) -> bool:
    if instance is None:
        return False
    return _seed_fixture_path(instance, index=index) is not None
