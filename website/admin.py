from django.contrib import admin, messages
from django.contrib.sites.admin import SiteAdmin as DjangoSiteAdmin
from django.contrib.sites.models import Site
from django import forms
from django.db import models
from app.widgets import CopyColorWidget
from django.shortcuts import redirect
from django.urls import path
import ipaddress
from django.apps import apps as django_apps
from django.conf import settings

from .models import SiteBadge, Application, SiteProxy, SiteApplication


def get_local_app_choices():
    choices = []
    for app_label in getattr(settings, "LOCAL_APPS", []):
        try:
            config = django_apps.get_app_config(app_label)
        except LookupError:
            continue
        choices.append((config.label, config.verbose_name))
    return choices


class SiteBadgeInline(admin.StackedInline):
    model = SiteBadge
    can_delete = False
    extra = 0
    formfield_overrides = {models.CharField: {"widget": CopyColorWidget}}
    fields = ("badge_color", "favicon")


class SiteApplicationInline(admin.TabularInline):
    model = SiteApplication
    extra = 0
    fields = ("application", "path", "is_default", "favicon")


class SiteAdmin(DjangoSiteAdmin):
    inlines = [SiteBadgeInline, SiteApplicationInline]
    change_list_template = "admin/sites/site/change_list.html"
    fields = ("domain", "name")
    list_display = ("domain", "name")

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "register-current/",
                self.admin_site.admin_view(self.register_current),
                name="website_siteproxy_register_current",
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
            name = "Terminal"
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
admin.site.register(SiteProxy, SiteAdmin)


class ApplicationForm(forms.ModelForm):
    name = forms.ChoiceField(choices=[])

    class Meta:
        model = Application
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].choices = get_local_app_choices()


class ApplicationSiteInline(admin.TabularInline):
    model = SiteApplication
    fk_name = "application"
    extra = 0


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    form = ApplicationForm
    list_display = ("name", "installed")
    readonly_fields = ("installed",)
    inlines = [ApplicationSiteInline]

    @admin.display(boolean=True)
    def installed(self, obj):
        return obj.installed


@admin.register(SiteApplication)
class SiteApplicationAdmin(admin.ModelAdmin):
    list_display = ("application", "site", "path", "is_default")
    list_filter = ("site", "application")
    fields = ("site", "application", "path", "is_default", "favicon")
