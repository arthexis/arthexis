import logging
from collections import deque
from pathlib import Path

from django.contrib import admin, messages
from django.contrib.sites.admin import SiteAdmin as DjangoSiteAdmin
from django.contrib.sites.models import Site
from django import forms
from django.shortcuts import redirect, render
from django.urls import NoReverseMatch, path, reverse
from django.utils.html import format_html

from django.template.response import TemplateResponse
from django.http import FileResponse, JsonResponse
from django.utils import timezone
from django.db.models import Count
from django.core.exceptions import FieldDoesNotExist, FieldError
from django.db.models.functions import TruncDate
from datetime import datetime, time, timedelta
import ipaddress
from django.apps import apps as django_apps
from django.conf import settings
from django.utils.translation import gettext_lazy as _, ngettext
from django.core.management import CommandError, call_command

from .site_config import ensure_site_fields
from .utils import landing_leads_supported

from .models import (
    SiteBadge,
    SiteTemplate,
    SiteProxy,
    Landing,
    LandingLead,
    ViewHistory,
    UserStory,
)
from apps.chats.models import ChatMessage, ChatSession
from apps.meta.models import WhatsAppChatBridge
from apps.odoo.models import OdooChatBridge
from apps.release.models import ReleaseManager
from apps.locals.user_data import EntityModelAdmin
from apps.app.models import (
    Application,
    ApplicationModel,
    refresh_application_models,
)
from apps.nodes.forms import NodeRoleMultipleChoiceField


logger = logging.getLogger(__name__)


class SiteBadgeInline(admin.StackedInline):
    model = SiteBadge
    can_delete = False
    extra = 0
    fields = ("favicon", "landing_override")


class SiteForm(forms.ModelForm):
    name = forms.CharField(required=False)

    class Meta:
        model = Site
        fields = "__all__"


ensure_site_fields()


class _BooleanAttributeListFilter(admin.SimpleListFilter):
    """Filter helper for boolean attributes on :class:`~django.contrib.sites.models.Site`."""

    field_name: str

    def lookups(self, request, model_admin):  # pragma: no cover - admin UI
        return (("1", _("Yes")), ("0", _("No")))

    def queryset(self, request, queryset):
        value = self.value()
        if value not in {"0", "1"}:
            return queryset
        expected = value == "1"
        try:
            return queryset.filter(**{self.field_name: expected})
        except FieldError:  # pragma: no cover - defensive when fields missing
            return queryset


class ManagedSiteListFilter(_BooleanAttributeListFilter):
    title = _("Managed by local NGINX")
    parameter_name = "managed"
    field_name = "managed"


class RequireHttpsListFilter(_BooleanAttributeListFilter):
    title = _("Require HTTPS")
    parameter_name = "require_https"
    field_name = "require_https"


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
                    _("%(fixture)s: %(error)s")
                    % {"fixture": path.name, "error": exc},
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
    class SiteTemplateAdminForm(forms.ModelForm):
        color_fields = (
            "primary_color",
            "primary_color_emphasis",
            "accent_color",
            "accent_color_emphasis",
            "support_color",
            "support_color_emphasis",
            "support_text_color",
        )

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            for field_name in self.color_fields:
                value = self.initial.get(field_name) or getattr(self.instance, field_name, None)
                if isinstance(value, str) and len(value) == 4:
                    # Preserve 3-digit shorthand colors by using a text widget that accepts #rgb.
                    self.fields[field_name].widget = forms.TextInput(attrs={"type": "text"})

        class Meta:
            model = SiteTemplate
            fields = "__all__"
            widgets = {
                "primary_color": forms.TextInput(attrs={"type": "color"}),
                "primary_color_emphasis": forms.TextInput(attrs={"type": "color"}),
                "accent_color": forms.TextInput(attrs={"type": "color"}),
                "accent_color_emphasis": forms.TextInput(attrs={"type": "color"}),
                "support_color": forms.TextInput(attrs={"type": "color"}),
                "support_color_emphasis": forms.TextInput(attrs={"type": "color"}),
                "support_text_color": forms.TextInput(attrs={"type": "color"}),
            }

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


class ApplicationForm(forms.ModelForm):
    name = forms.CharField(required=False)

    class Meta:
        model = Application
        fields = "__all__"


class ApplicationInstalledListFilter(admin.SimpleListFilter):
    title = _("Installed state")
    parameter_name = "installed"

    def lookups(self, request, model_admin):  # pragma: no cover - admin UI
        return (("1", _("Installed")), ("0", _("Not installed")))

    def queryset(self, request, queryset):  # pragma: no cover - admin UI
        value = self.value()
        if value not in {"0", "1"}:
            return queryset

        installed_labels = set()
        installed_names = set()
        for config in django_apps.get_app_configs():
            installed_labels.add(config.label)
            installed_names.add(config.name)
            installed_names.add(config.name.rsplit(".", 1)[-1])

        installed_values = installed_labels | installed_names
        if value == "1":
            return queryset.filter(name__in=installed_values)
        return queryset.exclude(name__in=installed_values)


class ApplicationModelInline(admin.TabularInline):
    model = ApplicationModel
    extra = 0
    can_delete = False
    fields = ("label", "model_name", "verbose_name", "wiki_url")
    readonly_fields = ("label", "model_name", "verbose_name")
    ordering = ("label",)

    def has_add_permission(self, request, obj=None):  # pragma: no cover - admin UI
        return False


@admin.register(Application)
class ApplicationAdmin(EntityModelAdmin):
    form = ApplicationForm
    list_display = (
        "name",
        "order",
        "importance",
        "app_verbose_name",
        "description",
        "installed",
    )
    search_fields = ("name", "description")
    readonly_fields = ("installed",)
    inlines = (ApplicationModelInline,)
    list_filter = (
        ApplicationInstalledListFilter,
        "order",
        "importance",
        "is_deleted",
        "is_seed_data",
        "is_user_data",
    )
    actions = ("discover_app_models",)

    @admin.display(description="Verbose name")
    def app_verbose_name(self, obj):
        return obj.verbose_name

    @admin.display(boolean=True)
    def installed(self, obj):
        return obj.installed

    @admin.action(description=_("Discover App Models"))
    def discover_app_models(self, request, queryset):
        refresh_application_models(using=queryset.db, applications=queryset)
        self.message_user(
            request,
            ngettext(
                "Discovered models for %(count)d application.",
                "Discovered models for %(count)d applications.",
                queryset.count(),
            )
            % {"count": queryset.count()},
            level=messages.SUCCESS,
        )


@admin.register(Landing)
class LandingAdmin(EntityModelAdmin):
    list_display = (
        "label",
        "path",
        "module",
        "enabled",
        "track_leads",
        "validation_status",
    )
    list_filter = (
        "enabled",
        "track_leads",
        "module__roles",
        "module__application",
    )
    search_fields = (
        "label",
        "path",
        "description",
        "module__path",
        "module__application__name",
    )
    fields = (
        "module",
        "path",
        "label",
        "enabled",
        "track_leads",
        "description",
        "validation_status",
        "validated_url_at",
    )
    readonly_fields = ("validation_status", "validated_url_at")
    list_select_related = ("module", "module__application")


@admin.register(LandingLead)
class LandingLeadAdmin(EntityModelAdmin):
    list_display = (
        "landing_label",
        "landing_path",
        "status",
        "user",
        "referer_display",
        "created_on",
    )
    list_filter = (
        "status",
        "landing__module__roles",
        "landing__module__application",
    )
    search_fields = (
        "landing__label",
        "landing__path",
        "referer",
        "path",
        "user__username",
        "user__email",
    )
    readonly_fields = (
        "landing",
        "user",
        "path",
        "referer",
        "user_agent",
        "ip_address",
        "created_on",
    )
    fields = (
        "landing",
        "user",
        "path",
        "referer",
        "user_agent",
        "ip_address",
        "status",
        "assign_to",
        "created_on",
    )
    list_select_related = ("landing", "landing__module", "landing__module__application")
    ordering = ("-created_on",)
    date_hierarchy = "created_on"

    def changelist_view(self, request, extra_context=None):
        if not landing_leads_supported():
            self.message_user(
                request,
                _(
                    "Landing leads are not being recorded because Celery is not running on this node."
                ),
                messages.WARNING,
            )
        return super().changelist_view(request, extra_context=extra_context)

    @admin.display(description=_("Landing"), ordering="landing__label")
    def landing_label(self, obj):
        return obj.landing.label

    @admin.display(description=_("Path"), ordering="landing__path")
    def landing_path(self, obj):
        return obj.landing.path

    @admin.display(description=_("Referrer"))
    def referer_display(self, obj):
        return obj.referer or ""


@admin.register(ViewHistory)
class ViewHistoryAdmin(EntityModelAdmin):
    date_hierarchy = "visited_at"
    list_display = (
        "path",
        "status_code",
        "status_text",
        "method",
        "visited_at",
    )
    list_filter = ("method", "status_code")
    search_fields = ("path", "error_message", "view_name", "status_text")
    readonly_fields = (
        "path",
        "method",
        "status_code",
        "status_text",
        "error_message",
        "view_name",
        "visited_at",
    )
    ordering = ("-visited_at",)
    change_list_template = "admin/pages/viewhistory/change_list.html"
    actions = ["view_traffic_graph"]

    def has_add_permission(self, request):
        return False

    @admin.action(description="View traffic graph")
    def view_traffic_graph(self, request, queryset):
        return redirect("admin:pages_viewhistory_traffic_graph")

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "traffic-graph/",
                self.admin_site.admin_view(self.traffic_graph_view),
                name="pages_viewhistory_traffic_graph",
            ),
            path(
                "traffic-data/",
                self.admin_site.admin_view(self.traffic_data_view),
                name="pages_viewhistory_traffic_data",
            ),
        ]
        return custom + urls

    def traffic_graph_view(self, request):
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": "Public site traffic",
            "chart_endpoint": reverse("admin:pages_viewhistory_traffic_data"),
        }
        return TemplateResponse(
            request,
            "admin/pages/viewhistory/traffic_graph.html",
            context,
        )

    def traffic_data_view(self, request):
        return JsonResponse(
            self._build_chart_data(days=self._resolve_requested_days(request))
        )

    def _resolve_requested_days(self, request, default: int = 30) -> int:
        raw_value = request.GET.get("days")
        if raw_value in (None, ""):
            return default

        try:
            days = int(raw_value)
        except (TypeError, ValueError):
            return default

        minimum = 1
        maximum = 90
        return max(minimum, min(days, maximum))

    def _build_chart_data(self, days: int = 30, max_pages: int = 8) -> dict:
        end_date = timezone.localdate()
        start_date = end_date - timedelta(days=days - 1)

        start_at = datetime.combine(start_date, time.min)
        end_at = datetime.combine(end_date + timedelta(days=1), time.min)

        if settings.USE_TZ:
            current_tz = timezone.get_current_timezone()
            start_at = timezone.make_aware(start_at, current_tz)
            end_at = timezone.make_aware(end_at, current_tz)
            trunc_expression = TruncDate("visited_at", tzinfo=current_tz)
        else:
            trunc_expression = TruncDate("visited_at")

        queryset = ViewHistory.objects.filter(
            visited_at__gte=start_at, visited_at__lt=end_at
        )

        meta = {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        }

        if not queryset.exists():
            meta["pages"] = []
            return {"labels": [], "datasets": [], "meta": meta}

        top_paths = list(
            queryset.values("path")
            .annotate(total=Count("id"))
            .order_by("-total")[:max_pages]
        )
        paths = [entry["path"] for entry in top_paths]
        meta["pages"] = paths

        labels = [
            (start_date + timedelta(days=offset)).isoformat() for offset in range(days)
        ]

        aggregates = (
            queryset.filter(path__in=paths)
            .annotate(day=trunc_expression)
            .values("day", "path")
            .order_by("day")
            .annotate(total=Count("id"))
        )

        counts: dict[str, dict[str, int]] = {
            path: {label: 0 for label in labels} for path in paths
        }
        for row in aggregates:
            day = row["day"].isoformat()
            path = row["path"]
            if day in counts.get(path, {}):
                counts[path][day] = row["total"]

        palette = [
            "#1f77b4",
            "#ff7f0e",
            "#2ca02c",
            "#d62728",
            "#9467bd",
            "#8c564b",
            "#e377c2",
            "#7f7f7f",
            "#bcbd22",
            "#17becf",
        ]
        datasets = []
        for index, path in enumerate(paths):
            color = palette[index % len(palette)]
            datasets.append(
                {
                    "label": path,
                    "data": [counts[path][label] for label in labels],
                    "borderColor": color,
                    "backgroundColor": color,
                    "fill": False,
                    "tension": 0.3,
                }
            )

        return {"labels": labels, "datasets": datasets, "meta": meta}


@admin.register(OdooChatBridge)
class OdooChatBridgeAdmin(EntityModelAdmin):
    list_display = ("bridge_label", "site", "channel_id", "is_enabled", "is_default")
    list_filter = ("is_enabled", "is_default", "site")
    search_fields = ("channel_uuid", "channel_id")
    ordering = ("site__domain", "channel_id")
    readonly_fields = ("is_seed_data", "is_user_data", "is_deleted")
    fieldsets = (
        (None, {"fields": ("site", "is_default", "profile", "is_enabled")}),
        (
            _("Odoo channel"),
            {"fields": ("channel_id", "channel_uuid", "notify_partner_ids")},
        ),
        (
            _("Flags"),
            {
                "fields": ("is_seed_data", "is_user_data", "is_deleted"),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.display(description=_("Bridge"))
    def bridge_label(self, obj):
        return str(obj)


@admin.register(WhatsAppChatBridge)
class WhatsAppChatBridgeAdmin(EntityModelAdmin):
    list_display = (
        "bridge_label",
        "site",
        "phone_number_id",
        "is_enabled",
        "is_default",
    )
    list_filter = ("is_enabled", "is_default", "site")
    search_fields = ("phone_number_id",)
    ordering = ("site__domain", "phone_number_id")
    readonly_fields = ("is_seed_data", "is_user_data", "is_deleted")
    fieldsets = (
        (None, {"fields": ("site", "is_default", "is_enabled")}),
        (
            _("WhatsApp client"),
            {"fields": ("api_base_url", "phone_number_id", "access_token")},
        ),
        (
            _("Flags"),
            {
                "fields": ("is_seed_data", "is_user_data", "is_deleted"),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.display(description=_("Bridge"))
    def bridge_label(self, obj):
        return str(obj)


class ChatMessageInline(admin.TabularInline):
    model = ChatMessage
    can_delete = False
    extra = 0
    fields = ("created_at", "author", "from_staff", "body")
    readonly_fields = fields
    ordering = ("created_at",)

    @admin.display(description=_("Author"))
    def author(self, obj):
        return obj.author_label()


@admin.register(ChatSession)
class ChatSessionAdmin(EntityModelAdmin):
    date_hierarchy = "created_at"
    list_display = (
        "uuid",
        "site",
        "whatsapp_number",
        "status",
        "last_activity_at",
        "last_visitor_activity_at",
        "last_staff_activity_at",
        "escalated_at",
    )
    list_filter = ("status", "site")
    search_fields = (
        "uuid",
        "visitor_key",
        "whatsapp_number",
        "user__username",
        "messages__body",
    )
    readonly_fields = (
        "uuid",
        "created_at",
        "updated_at",
        "last_activity_at",
        "last_visitor_activity_at",
        "last_staff_activity_at",
        "escalated_at",
        "closed_at",
        "visitor_key",
        "is_seed_data",
        "is_user_data",
        "is_deleted",
    )
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "uuid",
                    "status",
                    "site",
                    "user",
                    "visitor_key",
                    "whatsapp_number",
                )
            },
        ),
        (
            _("Activity"),
            {
                "fields": (
                    "created_at",
                    "updated_at",
                    "last_activity_at",
                    "last_visitor_activity_at",
                    "last_staff_activity_at",
                    "escalated_at",
                    "closed_at",
                ),
            },
        ),
        (
            _("Flags"),
            {
                "fields": ("is_seed_data", "is_user_data", "is_deleted"),
                "classes": ("collapse",),
            },
        ),
    )
    inlines = [ChatMessageInline]
    list_select_related = ("site", "user")
    ordering = ("-last_activity_at",)
    actions = ["close_sessions"]

    @admin.action(description=_("Close selected sessions"))
    def close_sessions(self, request, queryset):
        closed = 0
        for session in queryset:
            if session.status != session.Status.CLOSED:
                session.close()
                closed += 1
        if closed:
            self.message_user(
                request,
                ngettext(
                    "Closed %(count)d chat session.",
                    "Closed %(count)d chat sessions.",
                    closed,
                )
                % {"count": closed},
                messages.SUCCESS,
            )


@admin.register(UserStory)
class UserStoryAdmin(EntityModelAdmin):
    date_hierarchy = "submitted_at"
    actions = ["create_github_issues"]
    list_display = (
        "name",
        "language_code",
        "rating",
        "path",
        "status",
        "submitted_at",
        "github_issue_display",
        "owner",
        "assign_to",
    )
    list_filter = ("rating", "status", "submitted_at")
    search_fields = (
        "name",
        "comments",
        "path",
        "language_code",
        "referer",
        "github_issue_url",
        "ip_address",
    )
    readonly_fields = (
        "name",
        "rating",
        "comments",
        "path",
        "user",
        "owner",
        "language_code",
        "referer",
        "user_agent",
        "ip_address",
        "created_on",
        "submitted_at",
        "github_issue_number",
        "github_issue_url",
    )
    ordering = ("-submitted_at",)
    fields = (
        "name",
        "rating",
        "comments",
        "path",
        "language_code",
        "user",
        "owner",
        "status",
        "assign_to",
        "referer",
        "user_agent",
        "ip_address",
        "created_on",
        "submitted_at",
        "github_issue_number",
        "github_issue_url",
    )

    @admin.display(description=_("GitHub issue"), ordering="github_issue_number")
    def github_issue_display(self, obj):
        if obj.github_issue_url:
            label = (
                f"#{obj.github_issue_number}"
                if obj.github_issue_number is not None
                else obj.github_issue_url
            )
            return format_html(
                '<a href="{}" target="_blank" rel="noopener noreferrer">{}</a>',
                obj.github_issue_url,
                label,
            )
        if obj.github_issue_number is not None:
            return f"#{obj.github_issue_number}"
        return ""

    @admin.action(description=_("Create GitHub issues"))
    def create_github_issues(self, request, queryset):
        created = 0
        skipped = 0

        for story in queryset:
            if story.github_issue_url:
                skipped += 1
                continue

            try:
                issue_url = story.create_github_issue()
            except Exception as exc:  # pragma: no cover - network/runtime errors
                logger.exception("Failed to create GitHub issue for UserStory %s", story.pk)
                message = _("Unable to create a GitHub issue for %(story)s: %(error)s") % {
                    "story": story,
                    "error": exc,
                }

                if (
                    isinstance(exc, RuntimeError)
                    and "GitHub token is not configured" in str(exc)
                ):
                    try:
                        opts = ReleaseManager._meta
                        config_url = reverse(
                            f"{self.admin_site.name}:{opts.app_label}_{opts.model_name}_changelist"
                        )
                    except NoReverseMatch:  # pragma: no cover - defensive guard
                        config_url = None
                    if config_url:
                        message = format_html(
                            "{} <a href=\"{}\">{}</a>",
                            message,
                            config_url,
                            _("Configure GitHub credentials."),
                        )

                self.message_user(
                    request,
                    message,
                    messages.ERROR,
                )
                continue

            if issue_url:
                created += 1
            else:
                skipped += 1

        if created:
            self.message_user(
                request,
                ngettext(
                    "Created %(count)d GitHub issue.",
                    "Created %(count)d GitHub issues.",
                    created,
                )
                % {"count": created},
                messages.SUCCESS,
            )

        if skipped:
            self.message_user(
                request,
                ngettext(
                    "Skipped %(count)d feedback item (issue already exists or was throttled).",
                    "Skipped %(count)d feedback items (issues already exist or were throttled).",
                    skipped,
                )
                % {"count": skipped},
                messages.INFO,
            )

    def has_add_permission(self, request):
        return False



def _read_log_tail(path: Path, limit: int) -> str:
    """Return the last ``limit`` lines from ``path`` preserving newlines."""

    with path.open("r", encoding="utf-8") as handle:
        return "".join(deque(handle, maxlen=limit))


def log_viewer(request):
    logs_dir = Path(settings.BASE_DIR) / "logs"
    logs_exist = logs_dir.exists() and logs_dir.is_dir()
    available_logs = []
    if logs_exist:
        available_logs = sorted(
            [
                entry.name
                for entry in logs_dir.iterdir()
                if entry.is_file() and not entry.name.startswith(".")
            ],
            key=str.lower,
        )

    selected_log = request.GET.get("log", "")
    log_content = ""
    log_error = ""
    limit_options = [
        {"value": "20", "label": "20"},
        {"value": "40", "label": "40"},
        {"value": "100", "label": "100"},
        {"value": "all", "label": _("All")},
    ]
    allowed_limits = [item["value"] for item in limit_options]
    limit_choice = request.GET.get("limit", "20")
    if limit_choice not in allowed_limits:
        limit_choice = "20"
    limit_index = allowed_limits.index(limit_choice)
    download_requested = request.GET.get("download") == "1"

    if selected_log:
        if selected_log in available_logs:
            selected_path = logs_dir / selected_log
            try:
                if download_requested:
                    return FileResponse(
                        selected_path.open("rb"),
                        as_attachment=True,
                        filename=selected_log,
                    )
                if limit_choice == "all":
                    try:
                        log_content = selected_path.read_text(encoding="utf-8")
                    except UnicodeDecodeError:
                        log_content = selected_path.read_text(
                            encoding="utf-8", errors="replace"
                        )
                else:
                    try:
                        limit_value = int(limit_choice)
                    except (TypeError, ValueError):
                        limit_value = 20
                        limit_choice = "20"
                        limit_index = allowed_limits.index(limit_choice)
                    try:
                        log_content = _read_log_tail(selected_path, limit_value)
                    except UnicodeDecodeError:
                        with selected_path.open(
                            "r", encoding="utf-8", errors="replace"
                        ) as handle:
                            log_content = "".join(deque(handle, maxlen=limit_value))
            except OSError as exc:  # pragma: no cover - filesystem edge cases
                logger.warning("Unable to read log file %s", selected_path, exc_info=exc)
                log_error = _(
                    "The log file could not be read. Check server permissions and try again."
                )
        else:
            log_error = _("The requested log could not be found.")

    if not logs_exist:
        log_notice = _("The logs directory could not be found at %(path)s.") % {
            "path": logs_dir,
        }
    elif not available_logs:
        log_notice = _("No log files were found in %(path)s.") % {"path": logs_dir}
    else:
        log_notice = ""

    limit_label = limit_options[limit_index]["label"]
    context = {**admin.site.each_context(request)}
    context.update(
        {
            "title": _("Log viewer"),
            "available_logs": available_logs,
            "selected_log": selected_log,
            "log_content": log_content,
            "log_error": log_error,
            "log_notice": log_notice,
            "logs_directory": logs_dir,
            "log_limit_options": limit_options,
            "log_limit_index": limit_index,
            "log_limit_choice": limit_choice,
            "log_limit_label": limit_label,
        }
    )
    return TemplateResponse(request, "admin/log_viewer.html", context)


def get_admin_urls(original_get_urls):
    def get_urls():
        urls = original_get_urls()
        my_urls = [
            path(
                "logs/viewer/",
                admin.site.admin_view(log_viewer),
                name="log_viewer",
            ),
        ]
        return my_urls + urls

    return get_urls


admin.site.get_urls = get_admin_urls(admin.site.get_urls)
