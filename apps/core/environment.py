from __future__ import annotations

import os
from django.conf import settings
from django.contrib import admin
from django.template.response import TemplateResponse
from django.urls import path
from django.utils.translation import gettext_lazy as _


def _get_django_settings():
    return sorted(
        [(name, getattr(settings, name)) for name in dir(settings) if name.isupper()]
    )


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
    context = admin.site.each_context(request)
    context.update(
        {
            "title": _("Django Config"),
            "django_settings": _get_django_settings(),
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
