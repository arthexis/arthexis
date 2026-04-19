"""Helpers for coordinating expensive post-migrate work."""

from __future__ import annotations

from django.apps import apps as django_apps


def is_final_post_migrate_app(app_config) -> bool:
    """Return whether ``app_config`` is the final app for Django post-migrate."""

    if app_config is None:
        return False

    app_configs = tuple(django_apps.get_app_configs())
    if not app_configs:
        return False

    final_app_config = app_configs[-1]
    return (
        app_config.label == final_app_config.label
        and app_config.name == final_app_config.name
    )
