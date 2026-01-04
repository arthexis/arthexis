from __future__ import annotations

from django.contrib import admin

from apps.nginx.admin.certificates import CertificateGenerationMixin
from apps.nginx.admin.views import SiteConfigurationViewMixin
from apps.nginx.forms import SiteConfigurationForm
from apps.nginx.models import SiteConfiguration


@admin.register(SiteConfiguration)
class SiteConfigurationAdmin(
    CertificateGenerationMixin, SiteConfigurationViewMixin, admin.ModelAdmin
):
    form = SiteConfigurationForm

