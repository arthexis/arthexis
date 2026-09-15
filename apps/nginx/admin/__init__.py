from __future__ import annotations

from django.contrib import admin

from apps.nginx.admin.views import SiteConfigurationViewMixin
from apps.nginx.forms import SiteConfigurationForm
from apps.nginx.models import SiteConfiguration


@admin.register(SiteConfiguration)
class SiteConfigurationAdmin(SiteConfigurationViewMixin, admin.ModelAdmin):
    form = SiteConfigurationForm
