from __future__ import annotations

import os
from pathlib import Path

from django.conf import settings
from django.contrib import admin
from django.contrib import messages
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
    """
    if "_" not in setting_name:
        return setting_name
    return setting_name.split("_", 1)[0]


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
    user_env_values = _load_user_env_values(request.user)

    if request.method == "POST":
        loaded_values = _load_user_env_values(request.user)
        submitted_values = _extract_user_values(request, env_vars)
        loaded_values.update(submitted_values)
        _write_user_env_values(request.user, loaded_values)
        messages.success(
            request,
            _(
                "Personal environment values saved. They are applied after the next restart."
            ),
        )

    env_rows = [
        {
            "key": key,
            "value": value,
            "user_value": user_env_values.get(key, ""),
        }
        for key, value in env_vars
    ]
    context = admin.site.each_context(request)
    context.update(
        {
            "title": _("Environment"),
            "env_rows": env_rows,
            "environment_tasks": [],
        }
    )
    return TemplateResponse(request, "admin/environment.html", context)


def _user_env_dir() -> Path:
    """Return the directory used to store user-specific environment files."""
    return Path(settings.BASE_DIR) / "var" / "user_env"


def _user_env_path(user) -> Path:
    """Return the path to the current user's personal environment file."""
    return _user_env_dir() / f"{user.pk}.env"


def _load_user_env_values(user) -> dict[str, str]:
    """Load key/value pairs from the user's personal ``.env`` file."""
    env_path = _user_env_path(user)
    if not env_path.exists():
        return {}

    values: dict[str, str] = {}
    with env_path.open("r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()

            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            values[key] = value.strip()

    return values


def _extract_user_values(request, env_vars: list[tuple[str, str]]) -> dict[str, str]:
    """Extract submitted user values for known environment keys from ``request``."""
    submitted: dict[str, str] = {}
    for key, _ in env_vars:
        value = request.POST.get(f"user_value_{key}", "").strip()
        if value:
            submitted[key] = value
    return submitted



def _write_user_env_values(user, values: dict[str, str]) -> None:
    """Persist user-specific environment values to the personal ``.env`` file."""
    env_dir = _user_env_dir()
    env_dir.mkdir(parents=True, exist_ok=True)
    env_path = _user_env_path(user)

    with env_path.open("w", encoding="utf-8") as env_file:
        for key in sorted(values):
            # Remove newlines and carriage returns to prevent injection
            safe_value = values[key].replace('\n', '').replace('\r', '')
            env_file.write(f"{key}={safe_value}\n")


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
