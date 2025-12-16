from __future__ import annotations

from django.apps import AppConfig


class NginxConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.nginx"
    verbose_name = "NGINX"
