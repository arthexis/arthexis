from django.contrib import admin, messages
from django.contrib.sites.admin import SiteAdmin as DjangoSiteAdmin
from django.contrib.sites.models import Site
from django import forms
from django.db import models
from app.widgets import CopyColorWidget
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils.html import format_html
import ipaddress
from django.apps import apps as django_apps
from django.conf import settings

from nodes.models import Node
from nodes.utils import capture_screenshot, save_screenshot

from .models import SiteBadge, Application, SiteProxy, Module, Landing


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


class ModuleInline(admin.TabularInline):
    model = Module
    extra = 0
    fields = ("application", "path", "menu", "is_default", "favicon")


class SiteAdmin(DjangoSiteAdmin):
    inlines = [SiteBadgeInline, ModuleInline]
    change_list_template = "admin/sites/site/change_list.html"
    fields = ("domain", "name")
    list_display = ("domain", "name")
    actions = ["capture_screenshot"]

    @admin.action(description="Capture screenshot")
    def capture_screenshot(self, request, queryset):
        node = Node.get_local()
        for site in queryset:
            url = f"http://{site.domain}/"
            try:
                path = capture_screenshot(url)
                screenshot = save_screenshot(path, node=node, method="ADMIN")
            except Exception as exc:  # pragma: no cover - browser issues
                self.message_user(request, f"{site.domain}: {exc}", messages.ERROR)
                continue
            if screenshot:
                link = reverse(
                    "admin:nodes_nodescreenshot_change", args=[screenshot.pk]
                )
                self.message_user(
                    request,
                    format_html(
                        'Screenshot for {} saved. <a href="{}">View</a>',
                        site.domain,
                        link,
                    ),
                    messages.SUCCESS,
                )
            else:
                self.message_user(
                    request,
                    f"{site.domain}: duplicate screenshot; not saved",
                    messages.INFO,
                )

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


class ApplicationModuleInline(admin.TabularInline):
    model = Module
    fk_name = "application"
    extra = 0


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    form = ApplicationForm
    list_display = ("name", "installed")
    readonly_fields = ("installed",)
    inlines = [ApplicationModuleInline]

    @admin.display(boolean=True)
    def installed(self, obj):
        return obj.installed


class LandingInline(admin.TabularInline):
    model = Landing
    extra = 0
    fields = ("path", "label", "enabled", "description")


@admin.register(Module)
class ModuleAdmin(admin.ModelAdmin):
    list_display = ("application", "site", "path", "menu", "is_default")
    list_filter = ("site", "application")
    fields = ("site", "application", "path", "menu", "is_default", "favicon")
    inlines = [LandingInline]
