from django.contrib import admin, messages
from django.contrib.sites.admin import SiteAdmin as DjangoSiteAdmin
from django.contrib.sites.models import Site
from django import forms
from django.db import models
from app.widgets import CopyColorWidget
from django.shortcuts import redirect
from django.urls import path
from django.utils.html import format_html
import ipaddress
from django.apps import apps as django_apps
from django.conf import settings
import requests

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


class SiteApplicationInline(admin.TabularInline):
    model = SiteApplication
    extra = 0


class SiteAdmin(DjangoSiteAdmin):
    inlines = [SiteBadgeInline, SiteApplicationInline]
    change_list_template = "admin/sites/site/change_list.html"
    fields = ("domain", "name", "site_url")
    readonly_fields = ("site_url",)
    list_display = ("domain", "name", "site_url")
    actions = ["check_site_status"]

    @admin.display(description="URL")
    def site_url(self, obj):
        if obj is None:
            return ""
        url = f"http://{obj.domain}"
        return format_html('<a href="{0}" target="_blank">{0}</a>', url)

    def check_site_status(self, request, queryset):
        for site in queryset:
            url = f"http://{site.domain}"
            try:
                resp = requests.get(url, timeout=5)
            except requests.RequestException as exc:
                self.message_user(
                    request,
                    f"{site.domain} is down: {exc}",
                    level=messages.ERROR,
                )
                continue
            if resp.status_code == 200:
                self.message_user(
                    request, f"{site.domain} is up", level=messages.SUCCESS
                )
            else:
                self.message_user(
                    request,
                    f"{site.domain} returned {resp.status_code}",
                    level=messages.WARNING,
                )
    check_site_status.short_description = "Test selected sites"

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
            name = "website"
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
