from django.contrib import admin, messages
from django.contrib.sites.models import Site
from django.contrib.sites.admin import SiteAdmin as DjangoSiteAdmin
from django import forms
from django.db import models
from django.shortcuts import redirect
from django.urls import path
import ipaddress

from .models import SiteBadge, App


class SiteBadgeInline(admin.StackedInline):
    model = SiteBadge
    can_delete = False
    extra = 0
    formfield_overrides = {
        models.CharField: {"widget": forms.TextInput(attrs={"type": "color"})}
    }


class SiteAdmin(DjangoSiteAdmin):
    inlines = [SiteBadgeInline]
    change_list_template = "admin/sites/site/change_list.html"
    fields = ("domain", "name", "is_seed_data")
    list_display = ("domain", "name", "is_seed_data")

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "register-current/",
                self.admin_site.admin_view(self.register_current),
                name="sites_site_register_current",
            )
        ]
        return custom + urls

    def register_current(self, request):
        domain = request.get_host().split(":")[0]
        try:
            ipaddress.ip_address(domain)
        except ValueError:
            name = domain
        else:
            name = "localhost"
        site, created = Site.objects.get_or_create(
            domain=domain, defaults={"name": name}
        )
        if created:
            self.message_user(request, "Current domain registered", messages.SUCCESS)
        else:
            self.message_user(
                request, "Current domain already registered", messages.INFO
            )
        return redirect("..")


admin.site.unregister(Site)
admin.site.register(Site, SiteAdmin)


@admin.register(App)
class AppAdmin(admin.ModelAdmin):
    list_display = ("name", "site", "path", "is_default")
    list_filter = ("site",)
