from __future__ import annotations

import os
from django.conf import settings
from django.contrib import admin
from django.template.response import TemplateResponse
from django.urls import path
from django.utils.translation import gettext_lazy as _


def _get_django_settings():
    """Return sorted setting name/value pairs for uppercase Django settings."""
    return sorted(
        [(name, getattr(settings, name)) for name in dir(settings) if name.isupper()]
    )


def _split_prefix(setting_name: str) -> str:
    """Return the grouping prefix for a setting key.

    Keys are grouped by the segment before the first underscore.
    Empty prefixes are normalized to ``"Other"`` to avoid blank sections.
    """
    if "_" not in setting_name:
        return setting_name

    prefix = setting_name.split("_", 1)[0]
    if prefix:
        return prefix
    return "Other"


def _group_django_settings(settings_items: list[tuple[str, object]]) -> list[dict[str, object]]:
    """Group settings into sections when prefixes are repeated.

    A prefix becomes a named section when multiple settings share it.
    Settings without a repeated prefix are added to an ``Other`` section.
    """
    grouped: dict[str, list[tuple[str, object]]] = {}
    for key, value in settings_items:
        grouped.setdefault(_split_prefix(key), []).append((key, value))

    repeated_prefixes = {
        prefix for prefix, items in grouped.items() if len(items) > 1
    }
    sections: list[dict[str, object]] = []
    for prefix in sorted(repeated_prefixes):
        sections.append({"name": prefix, "settings": grouped[prefix]})

    other_settings: list[tuple[str, object]] = []
    for prefix, items in grouped.items():
        if prefix not in repeated_prefixes:
            other_settings.extend(items)

    if other_settings:
        sections.append({"name": _("Other"), "settings": sorted(other_settings)})

    return sections


def _environment_view(request):
    env_vars = sorted(os.environ.items())
    context = admin.site.each_context(request)
    context.update(
        {
            "title": _("Environment"),
            "env_vars": env_vars,
            "environment_tasks": [],
        }
    )
    return TemplateResponse(request, "admin/environment.html", context)

def _config_view(request):
    django_settings = _get_django_settings()
    context = admin.site.each_context(request)
    context.update(
        {
            "title": _("Django Config"),
            "config_sections": _group_django_settings(django_settings),
        }
    )
    return TemplateResponse(request, "admin/config.html", context)


def patch_admin_environment_view() -> None:
    """Register the Environment and Config admin views on the main admin site."""
    original_get_urls = admin.site.get_urls

    def get_urls():
        urls = original_get_urls()
        custom = [
            path(
                "environment/",
                admin.site.admin_view(_environment_view),
                name="environment",
            ),
            path(
                "config/",
                admin.site.admin_view(_config_view),
                name="config",
            ),
        ]
        return custom + urls

    admin.site.get_urls = get_urls
