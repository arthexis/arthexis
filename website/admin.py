from django.contrib import admin
from django.contrib.sites.models import Site
from django.contrib.sites.admin import SiteAdmin as DjangoSiteAdmin
from django import forms
from django.db import models

from .models import SiteBadge


class SiteBadgeInline(admin.StackedInline):
    model = SiteBadge
    can_delete = False
    extra = 0
    formfield_overrides = {
        models.CharField: {"widget": forms.TextInput(attrs={"type": "color"})}
    }


class SiteAdmin(DjangoSiteAdmin):
    inlines = [SiteBadgeInline]


admin.site.unregister(Site)
admin.site.register(Site, SiteAdmin)
