"""Utilities to keep migrations resilient to refactors."""

from __future__ import annotations

import importlib
import importlib.util
from typing import Any, Callable, Dict, Optional, Tuple

ModelKey = Tuple[str, str]


def _resolve_alias(target: Any, aliases: Optional[Dict[Any, Any]]) -> Any:
    if aliases and target in aliases:
        return aliases[target]
    return target


def import_callable(
    dotted_path: str,
    *,
    default: Optional[Callable[..., Any]] = None,
    aliases: Optional[Dict[str, str]] = None,
) -> Callable[..., Any]:
    """Import a callable for migrations without crashing on missing modules.

    If ``dotted_path`` (or its alias) cannot be imported and ``default`` is
    provided, the ``default`` callable is returned instead. Aliases let us
    redirect lookups after modules move without updating every migration.
    """

    target_path: str = _resolve_alias(dotted_path, aliases)

    if "." not in target_path:
        if default is not None:
            return default
        raise ImportError(
            "Callable paths for migrations must include a module name (e.g. 'package.callable')"
        )

    module_path, attribute = target_path.rsplit(".", 1)

    if importlib.util.find_spec(module_path) is None:
        if default is not None:
            return default
        raise ImportError(f"Module {module_path} could not be resolved for migrations")

    module = importlib.import_module(module_path)
    if hasattr(module, attribute):
        return getattr(module, attribute)

    if default is not None:
        return default

    raise ImportError(
        f"Callable {attribute} not found in {module_path} for migrations"
    )


def get_model(
    apps_registry: Any,
    app_label: str,
    model_name: str,
    *,
    allow_missing: bool = False,
    aliases: Optional[Dict[ModelKey, ModelKey]] = None,
) -> Any:
    """Fetch a model from the historical apps registry safely.

    The helper avoids raising ``LookupError`` when ``allow_missing`` is True,
    enabling older migrations to be skipped gracefully if the model was
    removed. Aliases allow redirects after app renames.
    """

    target_app, target_model = _resolve_alias((app_label, model_name), aliases)
    app_models = apps_registry.all_models.get(target_app, {})
    model = app_models.get(target_model.lower())

    if model is not None:
        return model

    if allow_missing:
        return None

    raise LookupError(f"Model {(app_label, model_name)} not present in apps registry")
