from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils.functional import LazyObject

from apps.core.entity import Entity

logger = logging.getLogger(__name__)


def _data_root(user=None) -> Path:
    path = Path(getattr(user, "data_path", "") or Path(settings.BASE_DIR) / "data")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _username_for(user) -> str:
    username = ""
    if hasattr(user, "get_username"):
        username = user.get_username()
    if not username and hasattr(user, "username"):
        username = user.username
    if not username and getattr(user, "pk", None):
        username = str(user.pk)
    return username


def user_allows_user_data(user) -> bool:
    if not user:
        return False
    username = _username_for(user)
    UserModel = get_user_model()
    system_username = getattr(UserModel, "SYSTEM_USERNAME", "")
    if system_username and username == system_username:
        return True
    return not getattr(user, "is_profile_restricted", False)


def _data_dir(user) -> Path:
    username = _username_for(user)
    if not username:
        raise ValueError("Cannot determine username for fixture directory")
    path = _data_root(user) / username
    path.mkdir(parents=True, exist_ok=True)
    return path


def fixture_path(user, instance) -> Path:
    model_meta = instance._meta.concrete_model._meta
    filename = f"{model_meta.app_label}_{model_meta.model_name}_{instance.pk}.json"
    return _data_dir(user) / filename


def _coerce_user(candidate, user_model):
    if candidate is None:
        return None
    if isinstance(candidate, user_model):
        return candidate
    if isinstance(candidate, LazyObject):
        try:
            candidate._setup()
        except Exception:
            return None
        return _coerce_user(candidate._wrapped, user_model)
    return None


def _select_fixture_user(candidate, user_model):
    user = _coerce_user(candidate, user_model)
    visited: set[int] = set()
    while user is not None:
        identifier = user.pk or id(user)
        if identifier in visited:
            break
        visited.add(identifier)
        username = _username_for(user)
        admin_username = getattr(user_model, "ADMIN_USERNAME", "")
        if admin_username and username == admin_username:
            try:
                delegate = getattr(user, "operate_as", None)
            except user_model.DoesNotExist:
                delegate = None
            else:
                delegate = _coerce_user(delegate, user_model)
            if delegate is not None and delegate is not user:
                user = delegate
                continue
        if user_allows_user_data(user):
            return user
        try:
            delegate = getattr(user, "operate_as", None)
        except user_model.DoesNotExist:
            delegate = None
        user = _coerce_user(delegate, user_model)
    return None


def resolve_fixture_user(instance, fallback=None):
    UserModel = get_user_model()
    owner = getattr(instance, "user", None)
    selected = _select_fixture_user(owner, UserModel)
    if selected is not None:
        return selected
    if hasattr(instance, "owner"):
        try:
            owner_value = instance.owner
        except Exception:
            owner_value = None
        else:
            selected = _select_fixture_user(owner_value, UserModel)
            if selected is not None:
                return selected
    selected = _select_fixture_user(fallback, UserModel)
    if selected is not None:
        return selected
    return fallback


def dump_user_fixture(instance, user=None) -> None:
    model = instance._meta.concrete_model
    UserModel = get_user_model()
    if issubclass(UserModel, Entity) and isinstance(instance, UserModel):
        return
    target_user = user or resolve_fixture_user(instance)
    if target_user is None:
        return
    allow_user_data = user_allows_user_data(target_user)
    if not allow_user_data:
        is_user_data = getattr(instance, "is_user_data", False)
        if not is_user_data and instance.pk:
            stored_flag = (
                type(instance)
                .all_objects.filter(pk=instance.pk)
                .values_list("is_user_data", flat=True)
                .first()
            )
            is_user_data = bool(stored_flag)
        if not is_user_data:
            return
    meta = model._meta
    path = fixture_path(target_user, instance)
    natural = getattr(model, "natural_key", None)
    if callable(natural):
        deps = getattr(natural, "dependencies", None)
        if isinstance(deps, (list, tuple, set)):
            normalized: list[str] = []
            updated = False
            for dep in deps:
                if isinstance(dep, str):
                    normalized.append(dep)
                elif hasattr(dep, "_meta") and getattr(dep._meta, "label_lower", None):
                    normalized.append(dep._meta.label_lower)
                    updated = True
                else:
                    normalized.append(dep)
            if updated:
                natural.dependencies = normalized
    call_command(
        "dumpdata",
        f"{meta.app_label}.{meta.model_name}",
        indent=2,
        pks=str(instance.pk),
        output=str(path),
        use_natural_foreign_keys=True,
    )


def delete_user_fixture(instance, user=None) -> None:
    target_user = user or resolve_fixture_user(instance)
    meta = instance._meta.concrete_model._meta
    filename = f"{meta.app_label}_{meta.model_name}_{instance.pk}.json"

    def _remove_for_user(candidate) -> None:
        if candidate is None:
            return
        base_path = Path(
            getattr(candidate, "data_path", "") or Path(settings.BASE_DIR) / "data"
        )
        username = _username_for(candidate)
        if not username:
            return
        user_dir = base_path / username
        if user_dir.exists():
            (user_dir / filename).unlink(missing_ok=True)

    if target_user is not None:
        _remove_for_user(target_user)
        return

    root = Path(settings.BASE_DIR) / "data"
    if root.exists():
        (root / filename).unlink(missing_ok=True)
        for path in root.iterdir():
            if path.is_dir():
                (path / filename).unlink(missing_ok=True)

    UserModel = get_user_model()
    manager = getattr(UserModel, "all_objects", UserModel._default_manager)
    for candidate in manager.all():
        data_path = getattr(candidate, "data_path", "")
        if not data_path:
            continue
        base_path = Path(data_path)
        if not base_path.exists():
            continue
        username = _username_for(candidate)
        if not username:
            continue
        user_dir = base_path / username
        if user_dir.exists():
            (user_dir / filename).unlink(missing_ok=True)


def _mark_fixture_user_data(path: Path) -> None:
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            content = path.read_bytes().decode("latin-1")
        except Exception:
            return
    except Exception:
        return
    try:
        data = json.loads(content)
    except Exception:
        return
    if not isinstance(data, list):
        return
    for obj in data:
        label = obj.get("model")
        if not label:
            continue
        try:
            model = apps.get_model(label)
        except LookupError:
            continue
        if not issubclass(model, Entity):
            continue
        pk = obj.get("pk")
        if pk is None:
            continue
        model.all_objects.filter(pk=pk).update(is_user_data=True)


def _fixture_entry_targets_installed_apps(obj) -> bool:
    """Return ``True`` when *obj* targets an installed app and model."""

    if not isinstance(obj, dict):
        return True

    label = obj.get("model")
    if not isinstance(label, str):
        return True
    if "." not in label:
        return False

    app_label, model_name = label.split(".", 1)
    if not app_label or not model_name:
        return False
    if app_label not in apps.app_configs and not apps.is_installed(app_label):
        return False
    try:
        apps.get_model(label)
    except LookupError:
        return False

    return True


def _fixture_entry_targets_user_data_model(obj) -> bool:
    """Return ``True`` when *obj* targets a model that supports user data."""

    if not isinstance(obj, dict):
        return False

    label = obj.get("model")
    if not isinstance(label, str):
        return False
    try:
        model = apps.get_model(label)
    except LookupError:
        return False

    return issubclass(model, Entity) or getattr(model, "supports_user_datum", False)


def _filter_fixture_entries(data: object) -> tuple[object, bool]:
    """Return filtered fixture data and whether anything was removed."""

    if not isinstance(data, list):
        return data, False

    filtered = [
        obj
        for obj in data
        if _fixture_entry_targets_installed_apps(obj)
        and _fixture_entry_targets_user_data_model(obj)
    ]
    return filtered, len(filtered) != len(data)


def _load_fixture(
    path: Path, *, mark_user_data: bool = True, verbosity: int = 0
) -> bool:
    """Load a fixture from *path* and optionally flag loaded entities."""

    text = None
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            text = path.read_bytes().decode("latin-1")
        except Exception:
            return False
        path.write_text(text, encoding="utf-8")
    except Exception:
        # Continue without cached text so ``call_command`` can surface the
        # underlying error just as before.
        pass

    temp_path = None
    try:
        if text is not None:
            try:
                data = json.loads(text)
            except Exception:
                data = None
            else:
                filtered, filtered_out = _filter_fixture_entries(data)
                if isinstance(filtered, list):
                    if not filtered:
                        if not data:
                            path.unlink(missing_ok=True)
                        return False
                    if filtered_out:
                        temp_file = tempfile.NamedTemporaryFile(
                            mode="w",
                            suffix=path.suffix,
                            delete=False,
                        )
                        json.dump(filtered, temp_file)
                        temp_file.close()
                        temp_path = Path(temp_file.name)

        try:
            verbosity_level = max(0, int(verbosity))
        except (TypeError, ValueError):
            verbosity_level = 0

        call_command(
            "load_user_data",
            str(temp_path or path),
            ignorenonexistent=True,
            verbosity=verbosity_level,
        )
    except Exception:
        return False
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    if mark_user_data:
        _mark_fixture_user_data(path)

    return True


def _fixture_sort_key(path: Path) -> tuple[int, str]:
    parts = path.name.split("_", 2)
    model_part = parts[1].lower() if len(parts) >= 2 else ""
    is_user = model_part == "user"
    return (0 if is_user else 1, path.name)


def _is_user_fixture(path: Path) -> bool:
    parts = path.name.split("_", 2)
    return len(parts) >= 2 and parts[1].lower() == "user"


_shared_fixtures_loaded = False


def load_shared_user_fixtures(*, force: bool = False, user=None) -> None:
    global _shared_fixtures_loaded
    if _shared_fixtures_loaded and not force:
        return
    root = _data_root(user)
    paths = sorted(root.glob("*.json"), key=_fixture_sort_key)
    loaded = 0
    for path in paths:
        if _is_user_fixture(path):
            continue
        if _load_fixture(path):
            loaded += 1
    if loaded:
        logger.info("Loaded %d shared user data fixture(s)", loaded)
    _shared_fixtures_loaded = True


def load_user_fixtures(user, *, include_shared: bool = False) -> None:
    if include_shared:
        load_shared_user_fixtures(user=user)
    paths = sorted(_data_dir(user).glob("*.json"), key=_fixture_sort_key)
    loaded = 0
    for path in paths:
        if _is_user_fixture(path):
            continue
        if _load_fixture(path):
            loaded += 1
    if loaded:
        username = _username_for(user) or "unknown user"
        logger.info("Loaded %d user data fixture(s) for %s", loaded, username)


def _user_fixture_paths(user):
    return [
        path
        for path in sorted(_data_dir(user).glob("*.json"), key=_fixture_sort_key)
        if not _is_user_fixture(path)
    ]


def _read_fixture_entries(path: Path) -> list[dict]:
    try:
        content_bytes = path.read_bytes()
    except (OSError, IOError):
        return []

    try:
        content = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        content = content_bytes.decode("latin-1")
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return []
    filtered, _ = _filter_fixture_entries(data)
    if not isinstance(filtered, list):
        return []
    return [obj for obj in filtered if isinstance(obj, dict)]


def _fixture_has_unapplied_entries(path: Path) -> bool:
    entries = _read_fixture_entries(path)
    pks_by_model: dict[type, list] = {}
    for obj in entries:
        label = obj.get("model")
        pk = obj.get("pk")
        if not label or pk is None:
            continue
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


def _user_fixture_status(user):
    paths = _user_fixture_paths(user)
    pending = [path for path in paths if _fixture_has_unapplied_entries(path)]
    return {"pending": pending, "total": paths}

