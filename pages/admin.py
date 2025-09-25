from django.contrib import admin, messages
from django.contrib.sites.admin import SiteAdmin as DjangoSiteAdmin
from django.contrib.sites.models import Site
from django import forms
from django.db import models
from core.widgets import CopyColorWidget
from django.shortcuts import redirect, render, get_object_or_404
from django.urls import path, reverse
from django.utils.html import format_html
from django.template.response import TemplateResponse
from django.http import JsonResponse
from django.utils import timezone
from django.db.models import Count
from django.db.models.functions import TruncDate
from datetime import timedelta
import ipaddress
from django.apps import apps as django_apps
from django.conf import settings

from nodes.models import Node
from nodes.utils import capture_screenshot, save_screenshot

from .models import (
    SiteBadge,
    Application,
    SiteProxy,
    Module,
    Landing,
    Favorite,
    ViewHistory,
    UserManual,
)
from django.contrib.contenttypes.models import ContentType
from core.user_data import EntityModelAdmin


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
    fields = ("badge_color", "favicon", "landing_override")


class SiteForm(forms.ModelForm):
    name = forms.CharField(required=False)

    class Meta:
        model = Site
        fields = "__all__"


class SiteAdmin(DjangoSiteAdmin):
    form = SiteForm
    inlines = [SiteBadgeInline]
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

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "register-current/",
                self.admin_site.admin_view(self.register_current),
                name="pages_siteproxy_register_current",
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
    fields = ("path", "label", "enabled", "description")


@admin.register(Module)
class ModuleAdmin(EntityModelAdmin):
    list_display = ("application", "node_role", "path", "menu", "is_default")
    list_filter = ("node_role", "application")
    fields = ("node_role", "application", "path", "menu", "is_default", "favicon")
    inlines = [LandingInline]


@admin.register(UserManual)
class UserManualAdmin(EntityModelAdmin):
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
        return JsonResponse(self._build_chart_data())

    def _build_chart_data(self, days: int = 30, max_pages: int = 8) -> dict:
        end_date = timezone.localdate()
        start_date = end_date - timedelta(days=days - 1)
        queryset = ViewHistory.objects.filter(
            visited_at__date__range=(start_date, end_date)
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
            .annotate(day=TruncDate("visited_at"))
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


def favorite_toggle(request, ct_id):
    ct = get_object_or_404(ContentType, pk=ct_id)
    fav = Favorite.objects.filter(user=request.user, content_type=ct).first()
    next_url = request.GET.get("next")
    if fav:
        return redirect(next_url or "admin:favorite_list")
    if request.method == "POST":
        label = request.POST.get("custom_label", "").strip()
        user_data = request.POST.get("user_data") == "on"
        Favorite.objects.create(
            user=request.user,
            content_type=ct,
            custom_label=label,
            user_data=user_data,
        )
        return redirect(next_url or "admin:index")
    return render(
        request,
        "admin/favorite_confirm.html",
        {"content_type": ct, "next": next_url},
    )


def favorite_list(request):
    favorites = Favorite.objects.filter(user=request.user).select_related(
        "content_type"
    )
    if request.method == "POST":
        selected = request.POST.getlist("user_data")
        for fav in favorites:
            fav.user_data = str(fav.pk) in selected
            fav.save(update_fields=["user_data"])
        return redirect("admin:favorite_list")
    return render(request, "admin/favorite_list.html", {"favorites": favorites})


def favorite_delete(request, pk):
    fav = get_object_or_404(Favorite, pk=pk, user=request.user)
    fav.delete()
    return redirect("admin:favorite_list")


def favorite_clear(request):
    Favorite.objects.filter(user=request.user).delete()
    return redirect("admin:favorite_list")


def get_admin_urls(original_get_urls):
    def get_urls():
        urls = original_get_urls()
        my_urls = [
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
