import logging
from collections import deque
from pathlib import Path

from django.contrib import admin, messages
from django.contrib.sites.admin import SiteAdmin as DjangoSiteAdmin
from django.contrib.sites.models import Site
from django import forms
from django.shortcuts import redirect, render, get_object_or_404
from django.urls import NoReverseMatch, path, reverse
from django.utils.html import format_html

from django.template.response import TemplateResponse
from django.http import FileResponse, JsonResponse
from django.utils import timezone
from django.db.models import Count
from django.core.exceptions import FieldError
from django.db.models.functions import TruncDate
from datetime import datetime, time, timedelta
import ipaddress
from django.apps import apps as django_apps
from django.conf import settings
from django.utils.translation import gettext_lazy as _, ngettext
from django.core.management import CommandError, call_command
from django.utils.http import url_has_allowed_host_and_scheme

from nodes.models import Node, NodeRole
from nodes.utils import capture_screenshot, save_screenshot

from .forms import UserManualAdminForm
from .module_defaults import reload_default_modules as restore_default_modules
from .site_config import ensure_site_fields
from .utils import landing_leads_supported

from .models import (
    SiteBadge,
    Application,
    SiteProxy,
    Module,
    Landing,
    LandingLead,
    RoleLanding,
    Favorite,
    ViewHistory,
    UserManual,
    UserStory,
)
from django.contrib.contenttypes.models import ContentType
from core.models import ReleaseManager
from core.user_data import EntityModelAdmin


logger = logging.getLogger(__name__)


def _get_safe_next_url(request):
    """Return a sanitized ``next`` parameter for redirect targets."""

    candidate = request.POST.get("next") or request.GET.get("next")
    if not candidate:
        return None

    allowed_hosts = {request.get_host()}
    allowed_hosts.update(filter(None, settings.ALLOWED_HOSTS))

    if url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts=allowed_hosts,
        require_https=request.is_secure(),
    ):
        return candidate
    return None


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
    fields = ("domain", "name", "managed", "require_https")
    list_display = ("domain", "name", "managed", "require_https")
    list_filter = (ManagedSiteListFilter, RequireHttpsListFilter)
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
                link = reverse("admin:nodes_contentsample_change", args=[screenshot.pk])
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

    def has_add_permission(self, request):
        if super().has_add_permission(request):
            return True
        return request.user.has_perm("sites.add_site")

    def has_change_permission(self, request, obj=None):
        if super().has_change_permission(request, obj=obj):
            return True
        return request.user.has_perm("sites.change_site")

    def has_delete_permission(self, request, obj=None):
        if super().has_delete_permission(request, obj=obj):
            return True
        return request.user.has_perm("sites.delete_site")

    def has_view_permission(self, request, obj=None):
        if super().has_view_permission(request, obj=obj):
            return True
        return request.user.has_perm("sites.view_site") or request.user.has_perm(
            "sites.change_site"
        )

    def has_module_permission(self, request):
        if super().has_module_permission(request):
            return True
        return request.user.has_module_perms("sites")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if {"managed", "require_https"} & set(form.changed_data or []):
            self.message_user(
                request,
                _(
                    "Managed NGINX configuration staged. Run network-setup.sh to apply changes."
                ),
                messages.INFO,
            )

    def delete_model(self, request, obj):
        super().delete_model(request, obj)
        self.message_user(
            request,
            _(
                "Managed NGINX configuration staged. Run network-setup.sh to apply changes."
            ),
            messages.INFO,
        )

    def _reload_site_fixtures(self, request):
        fixtures_dir = Path(settings.BASE_DIR) / "core" / "fixtures"
        fixture_paths = sorted(fixtures_dir.glob("references__00_site_*.json"))
        sigil_fixture = fixtures_dir / "sigil_roots__site.json"
        if sigil_fixture.exists():
            fixture_paths.append(sigil_fixture)

        if not fixture_paths:
            self.message_user(request, _("No site fixtures found."), messages.WARNING)
            return None

        loaded = 0
        for path in fixture_paths:
            try:
                call_command("loaddata", str(path), verbosity=0)
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
class ApplicationAdmin(EntityModelAdmin):
    form = ApplicationForm
    list_display = ("name", "app_verbose_name", "description", "installed")
    readonly_fields = ("installed",)
    inlines = [ApplicationModuleInline]

    @admin.display(description="Verbose name")
    def app_verbose_name(self, obj):
        return obj.verbose_name

    @admin.display(boolean=True)
    def installed(self, obj):
        return obj.installed


class LandingInline(admin.TabularInline):
    model = Landing
    extra = 0
    fields = ("path", "label", "enabled", "track_leads")
    show_change_link = True


@admin.register(Landing)
class LandingAdmin(EntityModelAdmin):
    list_display = ("label", "path", "module", "enabled", "track_leads")
    list_filter = (
        "enabled",
        "track_leads",
        "module__node_role",
        "module__application",
    )
    search_fields = (
        "label",
        "path",
        "description",
        "module__path",
        "module__application__name",
        "module__node_role__name",
    )
    fields = ("module", "path", "label", "enabled", "track_leads", "description")
    list_select_related = ("module", "module__application", "module__node_role")


@admin.register(Module)
class ModuleAdmin(EntityModelAdmin):
    change_list_template = "admin/pages/module/change_list.html"
    list_display = ("application", "node_role", "path", "menu", "is_default")
    list_filter = ("node_role", "application")
    fields = ("node_role", "application", "path", "menu", "is_default", "favicon")
    inlines = [LandingInline]

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "reload-default-modules/",
                self.admin_site.admin_view(self.reload_default_modules_view),
                name="pages_module_reload_default_modules",
            ),
        ]
        return custom + urls

    def reload_default_modules_view(self, request):
        if request.method != "POST":
            return redirect("..")

        summary = restore_default_modules(Application, Module, Landing, NodeRole)

        if summary.roles_processed == 0:
            self.message_user(
                request,
                _("No default modules were reloaded because the required node roles are missing."),
                messages.WARNING,
            )
        elif summary.has_changes:
            parts: list[str] = []
            if summary.modules_created:
                parts.append(
                    ngettext(
                        "%(count)d module created",
                        "%(count)d modules created",
                        summary.modules_created,
                    )
                    % {"count": summary.modules_created}
                )
            if summary.modules_updated:
                parts.append(
                    ngettext(
                        "%(count)d module updated",
                        "%(count)d modules updated",
                        summary.modules_updated,
                    )
                    % {"count": summary.modules_updated}
                )
            if summary.landings_created:
                parts.append(
                    ngettext(
                        "%(count)d landing created",
                        "%(count)d landings created",
                        summary.landings_created,
                    )
                    % {"count": summary.landings_created}
                )
            if summary.landings_updated:
                parts.append(
                    ngettext(
                        "%(count)d landing updated",
                        "%(count)d landings updated",
                        summary.landings_updated,
                    )
                    % {"count": summary.landings_updated}
                )

            details = "; ".join(parts)
            if details:
                message = _(
                    "Reloaded default modules for %(roles)d role(s). %(details)s."
                ) % {"roles": summary.roles_processed, "details": details}
            else:
                message = _(
                    "Reloaded default modules for %(roles)d role(s)."
                ) % {"roles": summary.roles_processed}
            self.message_user(request, message, messages.SUCCESS)
        else:
            self.message_user(
                request,
                _(
                    "Default modules are already up to date for %(roles)d role(s)."
                )
                % {"roles": summary.roles_processed},
                messages.INFO,
            )

        return redirect("..")


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
        "landing__module__node_role",
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


@admin.register(RoleLanding)
class RoleLandingAdmin(EntityModelAdmin):
    list_display = (
        "target_display",
        "landing_path",
        "landing_label",
        "priority",
        "is_seed_data",
    )
    list_filter = ("node_role", "security_group")
    search_fields = (
        "node_role__name",
        "security_group__name",
        "user__username",
        "landing__path",
        "landing__label",
    )
    fields = ("node_role", "security_group", "user", "priority", "landing")
    list_select_related = (
        "node_role",
        "security_group",
        "user",
        "landing",
        "landing__module",
    )

    @admin.display(description="Landing Path")
    def landing_path(self, obj):
        return obj.landing.path if obj.landing_id else ""

    @admin.display(description="Landing Label")
    def landing_label(self, obj):
        return obj.landing.label if obj.landing_id else ""

    @admin.display(description="Target", ordering="priority")
    def target_display(self, obj):
        if obj.node_role_id:
            return obj.node_role.name
        if obj.security_group_id:
            return obj.security_group.name
        if obj.user_id:
            return obj.user.get_username()
        return ""


@admin.register(UserManual)
class UserManualAdmin(EntityModelAdmin):
    form = UserManualAdminForm
    list_display = ("title", "slug", "languages", "is_seed_data", "is_user_data")
    search_fields = ("title", "slug", "description")
    list_filter = ("is_seed_data", "is_user_data")


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
        "screenshot_display",
        "take_screenshot",
        "owner",
        "assign_to",
    )
    list_filter = ("rating", "status", "submitted_at", "take_screenshot")
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
        "take_screenshot",
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
        "screenshot_display",
    )
    ordering = ("-submitted_at",)
    fields = (
        "name",
        "rating",
        "comments",
        "take_screenshot",
        "screenshot_display",
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

    @admin.display(description=_("Screenshot"), ordering="screenshot")
    def screenshot_display(self, obj):
        if not obj.screenshot_id:
            return ""
        try:
            url = reverse("admin:nodes_contentsample_change", args=[obj.screenshot_id])
        except NoReverseMatch:
            return obj.screenshot.path
        return format_html(
            '<a href="{}" target="_blank" rel="noopener noreferrer">{}</a>',
            url,
            _("View screenshot"),
        )
        return _("Not created")

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


def favorite_toggle(request, ct_id):
    ct = get_object_or_404(ContentType, pk=ct_id)
    fav = Favorite.objects.filter(user=request.user, content_type=ct).first()
    next_url = _get_safe_next_url(request)
    if request.method == "POST":
        if fav and request.POST.get("remove"):
            fav.delete()
            return redirect(next_url or "admin:index")
        label = request.POST.get("custom_label", "").strip()
        user_data = request.POST.get("user_data") == "on"
        priority_raw = request.POST.get("priority", "").strip()
        if fav:
            default_priority = fav.priority
        else:
            default_priority = 0
        if priority_raw:
            try:
                priority = int(priority_raw)
            except (TypeError, ValueError):
                priority = default_priority
        else:
            priority = default_priority

        if fav:
            update_fields = []
            if fav.custom_label != label:
                fav.custom_label = label
                update_fields.append("custom_label")
            if fav.user_data != user_data:
                fav.user_data = user_data
                update_fields.append("user_data")
            if fav.priority != priority:
                fav.priority = priority
                update_fields.append("priority")
            if update_fields:
                fav.save(update_fields=update_fields)
        else:
            Favorite.objects.create(
                user=request.user,
                content_type=ct,
                custom_label=label,
                user_data=user_data,
                priority=priority,
            )
        return redirect(next_url or "admin:index")
    return render(
        request,
        "admin/favorite_confirm.html",
        {
            "content_type": ct,
            "favorite": fav,
            "next": next_url,
            "initial_label": fav.custom_label if fav else "",
            "initial_priority": fav.priority if fav else 0,
            "is_checked": fav.user_data if fav else True,
        },
    )


def favorite_list(request):
    favorites = (
        Favorite.objects.filter(user=request.user)
        .select_related("content_type")
        .order_by("priority", "pk")
    )
    if request.method == "POST":
        selected = set(request.POST.getlist("user_data"))
        for fav in favorites:
            update_fields = []
            user_selected = str(fav.pk) in selected
            if fav.user_data != user_selected:
                fav.user_data = user_selected
                update_fields.append("user_data")

            priority_raw = request.POST.get(f"priority_{fav.pk}", "").strip()
            if priority_raw:
                try:
                    priority = int(priority_raw)
                except (TypeError, ValueError):
                    priority = fav.priority
                else:
                    if fav.priority != priority:
                        fav.priority = priority
                        update_fields.append("priority")
            else:
                if fav.priority != 0:
                    fav.priority = 0
                    update_fields.append("priority")

            if update_fields:
                fav.save(update_fields=update_fields)
        return redirect("admin:favorite_list")
    return render(request, "admin/favorite_list.html", {"favorites": favorites})


def favorite_delete(request, pk):
    fav = get_object_or_404(Favorite, pk=pk, user=request.user)
    fav.delete()
    return redirect("admin:favorite_list")


def favorite_clear(request):
    Favorite.objects.filter(user=request.user).delete()
    return redirect("admin:favorite_list")


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
            path(
                "favorites/<int:ct_id>/",
                admin.site.admin_view(favorite_toggle),
                name="favorite_toggle",
            ),
            path(
                "favorites/", admin.site.admin_view(favorite_list), name="favorite_list"
            ),
            path(
                "favorites/delete/<int:pk>/",
                admin.site.admin_view(favorite_delete),
                name="favorite_delete",
            ),
            path(
                "favorites/clear/",
                admin.site.admin_view(favorite_clear),
                name="favorite_clear",
            ),
        ]
        return my_urls + original_get_urls()

    return get_urls


admin.site.get_urls = get_admin_urls(admin.site.get_urls)
