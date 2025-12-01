from __future__ import annotations

import logging
from functools import lru_cache
from importlib import import_module
from typing import Callable

from django.apps import apps as django_apps

if False:  # pragma: no cover - typing only
    from .models import Node


logger = logging.getLogger(__name__)


def _iter_feature_modules():
    """Yield installed app modules that expose node feature hooks."""

    for config in django_apps.get_app_configs():
        module_path = f"{config.name}.node_features"
        try:
            yield import_module(module_path)
        except ModuleNotFoundError as exc:
            if exc.name == module_path:
                continue
            raise
        except Exception:
            logger.exception(
                "Unable to import node feature hooks from %s", module_path
            )


@lru_cache(maxsize=1)
def _get_feature_hooks() -> tuple[tuple[Callable, ...], tuple[Callable, ...]]:
    check_hooks: list[Callable] = []
    setup_hooks: list[Callable] = []
    for module in _iter_feature_modules():
        check = getattr(module, "check_node_feature", None)
        setup = getattr(module, "setup_node_feature", None)
        if callable(check):
            check_hooks.append(check)
        if callable(setup):
            setup_hooks.append(setup)
    return tuple(check_hooks), tuple(setup_hooks)


def _iter_hooks(attribute: str) -> tuple[Callable, ...]:
    checks, setups = _get_feature_hooks()
    hooks = checks if attribute == "check" else setups
    return hooks


def _invoke_hook(hook: Callable, slug: str, node: "Node"):
    try:
        return hook(slug=slug, node=node)
    except TypeError:
        # Support positional signatures for backwards compatibility.
        return hook(slug, node)


def run_feature_checks(slug: str, *, node: "Node") -> bool | None:
    """Run registered feature check hooks until one handles ``slug``."""

    for hook in _iter_hooks("check"):
        try:
            result = _invoke_hook(hook, slug, node)
        except Exception:
            logger.exception("Node feature check failed for %s", slug)
            continue
        if result is not None:
            return bool(result)
    return None


def run_feature_setups(slug: str, *, node: "Node") -> bool | None:
    """Run registered feature setup hooks until one handles ``slug``."""

    for hook in _iter_hooks("setup"):
        try:
            result = _invoke_hook(hook, slug, node)
        except Exception:
            logger.exception("Node feature setup failed for %s", slug)
            continue
        if result is not None:
            return bool(result)
    return None


__all__ = [
    "run_feature_checks",
    "run_feature_setups",
]
