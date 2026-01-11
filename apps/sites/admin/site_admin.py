import ipaddress
from pathlib import Path

from django import forms
from django.contrib import admin, messages
from django.contrib.sites.admin import SiteAdmin as DjangoSiteAdmin
from django.contrib.sites.models import Site
from django.core.exceptions import FieldDoesNotExist
from django.core.management import CommandError, call_command
from django.shortcuts import redirect
from django.urls import path
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _, ngettext
from django.conf import settings

from apps.locals.user_data import EntityModelAdmin

from apps.media.models import MediaFile
from apps.media.utils import create_media_file
from ..models import SiteBadge, SiteTemplate, SiteProxy, get_site_badge_favicon_bucket
from ..site_config import ensure_site_fields
from .filters import ManagedSiteListFilter, RequireHttpsListFilter
from .forms import SiteForm, SiteTemplateAdminForm


class SiteBadgeInlineForm(forms.ModelForm):
    favicon_upload = forms.ImageField(
        required=False,
        label=_("Favicon upload"),
        help_text=_("Upload a site-specific favicon."),
    )

    class Meta:
        model = SiteBadge
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        bucket = get_site_badge_favicon_bucket()
        self.fields["favicon_media"].queryset = MediaFile.objects.filter(bucket=bucket)

    def save(self, commit=True):
        instance = super().save(commit=False)
        upload = self.cleaned_data.get("favicon_upload")
        if upload:
            bucket = get_site_badge_favicon_bucket()
            instance.favicon_media = create_media_file(bucket=bucket, uploaded_file=upload)
        if commit:
            instance.save()
            self.save_m2m()
        return instance

    def clean_favicon_upload(self):
        upload = self.cleaned_data.get("favicon_upload")
        if upload:
            bucket = get_site_badge_favicon_bucket()
            if not bucket.allows_filename(upload.name):
                raise forms.ValidationError(_("File type is not allowed."))
            if not bucket.allows_size(upload.size):
                raise forms.ValidationError(_("File exceeds the allowed size."))
        return upload


class SiteBadgeInline(admin.StackedInline):
    model = SiteBadge
    form = SiteBadgeInlineForm
    can_delete = False
    extra = 0
    fields = ("favicon_media", "favicon_upload", "favicon_metadata", "landing_override")
    readonly_fields = ("favicon_metadata",)

    @admin.display(description=_("Favicon metadata"))
    def favicon_metadata(self, obj):
        media = getattr(obj, "favicon_media", None)
        if not media:
            return _("No favicon uploaded")
        return _("%(name)s (%(type)s, %(size)s bytes)") % {
            "name": media.original_name or media.file.name,
            "type": media.content_type or _("unknown"),
            "size": media.size,
        }


ensure_site_fields()


class SiteAdmin(DjangoSiteAdmin):
    form = SiteForm
    inlines = [SiteBadgeInline]
    change_list_template = "admin/sites/site/change_list.html"
    fields = (
        "domain",
        "name",
        "template",
        "default_landing",
        "managed",
        "require_https",
    )
    list_display = (
        "domain",
        "name",
        "template",
        "default_landing",
        "managed",
        "require_https",
    )
    list_select_related = ()
    list_filter = (ManagedSiteListFilter, RequireHttpsListFilter)

    def _has_siteproxy_permission(self, request, action: str) -> bool:
        """Return True when the user has the requested proxy or sites perm."""

        meta = self.model._meta
        proxy_perm = f"{meta.app_label}.{action}_{meta.model_name}"
        site_perm = f"sites.{action}_site"
        return request.user.has_perm(proxy_perm) or request.user.has_perm(site_perm)

    def has_add_permission(self, request):
        if super().has_add_permission(request):
            return True
        return self._has_siteproxy_permission(request, "add")

    def has_change_permission(self, request, obj=None):
        if super().has_change_permission(request, obj=obj):
            return True
        return self._has_siteproxy_permission(request, "change")

    def has_delete_permission(self, request, obj=None):
        if super().has_delete_permission(request, obj=obj):
            return True
        return self._has_siteproxy_permission(request, "delete")

    def has_view_permission(self, request, obj=None):
        if super().has_view_permission(request, obj=obj):
            return True
        return self._has_siteproxy_permission(request, "view") or self._has_siteproxy_permission(
            request, "change"
        )

    def has_module_permission(self, request):
        if super().has_module_permission(request):
            return True
        meta = self.model._meta
        return request.user.has_module_perms(meta.app_label) or request.user.has_module_perms(
            "sites"
        )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if {"managed", "require_https"} & set(form.changed_data or []):
            self.message_user(
                request,
                _(
                    "Managed NGINX configuration staged. Apply the changes through your deployment tooling."
                ),
                messages.INFO,
            )

    def delete_model(self, request, obj):
        super().delete_model(request, obj)
        self.message_user(
            request,
            _(
                "Managed NGINX configuration staged. Apply the changes through your deployment tooling."
            ),
            messages.INFO,
        )

    def get_queryset(self, request):
        ensure_site_fields()
        queryset = super().get_queryset(request)
        try:
            Site._meta.get_field("default_landing")
        except FieldDoesNotExist:
            return queryset
        # The optional ``default_landing`` field is injected at runtime. Avoid
        # applying ``select_related`` because the relation may not always be fully
        # configured on proxy models, which can raise ``FieldError`` during query
        # evaluation. Returning the base queryset keeps the change list working even
        # when the field is unavailable.
        return queryset

    def _reload_site_fixtures(self, request):
        fixtures_dir = Path(settings.BASE_DIR) / "apps" / "links" / "fixtures"
        fixture_paths = sorted(fixtures_dir.glob("references__00_site_*.json"))
        sigil_fixture = Path("apps/sigils/fixtures/sigil_roots__site.json")
        if sigil_fixture.exists():
            fixture_paths.append(sigil_fixture)

        if not fixture_paths:
            self.message_user(request, _("No site fixtures found."), messages.WARNING)
            return None

        loaded = 0
        for path in fixture_paths:
            try:
                call_command("load_user_data", str(path), verbosity=0)
            except CommandError as exc:
                self.message_user(
                    request,
                    _("%(fixture)s: %(error)s") % {"fixture": path.name, "error": exc},
                    messages.ERROR,
                )
            else:
                loaded += 1

        if loaded:
            message = ngettext(
                "Reloaded %(count)d site fixture.",
                "Reloaded %(count)d site fixtures.",
                loaded,
            ) % {"count": loaded}
            self.message_user(request, message, messages.SUCCESS)

        return None

    def reload_site_fixtures(self, request):
        if request.method != "POST":
            return redirect("..")

        self._reload_site_fixtures(request)

        return redirect("..")

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "register-current/",
                self.admin_site.admin_view(self.register_current),
                name="pages_siteproxy_register_current",
            ),
            path(
                "reload-site-fixtures/",
                self.admin_site.admin_view(self.reload_site_fixtures),
                name="pages_siteproxy_reload_site_fixtures",
            ),
        ]
        return custom + urls

    def register_current(self, request):
        domain = request.get_host().split(":")[0]
        try:
            ipaddress.ip_address(domain)
        except ValueError:
            name = domain
        else:
            name = ""
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


@admin.register(SiteTemplate)
class SiteTemplateAdmin(EntityModelAdmin):
    list_display = (
        "name",
        "palette",
        "primary_color",
        "accent_color",
        "support_color",
    )
    form = SiteTemplateAdminForm
    search_fields = ("name",)
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "name",
                    ("primary_color", "primary_color_emphasis"),
                    ("accent_color", "accent_color_emphasis"),
                    ("support_color", "support_color_emphasis", "support_text_color"),
                )
            },
        ),
    )

    @staticmethod
    def _render_swatch(color: str):  # pragma: no cover - admin rendering
        return format_html(
            '<span style="display:inline-block;width:1.35rem;height:1.35rem;'
            'border-radius:0.35rem;border:1px solid rgba(0,0,0,0.12);'
            'background:{};margin-right:0.2rem;"></span>',
            color,
        )

    def palette(self, obj):  # pragma: no cover - admin rendering
        colors = (
            obj.primary_color,
            obj.primary_color_emphasis,
            obj.accent_color,
            obj.accent_color_emphasis,
            obj.support_color,
            obj.support_color_emphasis,
            obj.support_text_color,
        )
        swatches = (self._render_swatch(color) for color in colors if color)
        return format_html("".join(swatches))

    palette.short_description = _("Palette")
