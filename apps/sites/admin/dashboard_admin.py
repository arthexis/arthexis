"""Admin dashboard async hydration helpers."""

from django.contrib import admin
from django.template.response import TemplateResponse
from django.urls import path


def dashboard_app_groups(request):
    """Render admin app groups for deferred dashboard hydration."""

    context = {
        **admin.site.each_context(request),
        "app_list": admin.site.get_app_list(request),
        "enable_app_visibility_controls": True,
    }
    return TemplateResponse(request, "admin/includes/dashboard_app_groups.html", context)


def patch_dashboard_app_groups_url() -> None:
    """Register admin URL for loading dashboard app groups asynchronously."""

    if getattr(admin.site, "_dashboard_app_groups_patched", False):
        return

    original_get_urls = admin.site.get_urls

    def get_urls():
        urls = original_get_urls()
        my_urls = [
            path(
                "dashboard/app-groups/",
                admin.site.admin_view(dashboard_app_groups),
                name="dashboard_app_groups",
            ),
        ]
        return my_urls + urls

    admin.site.get_urls = get_urls
    admin.site._dashboard_app_groups_patched = True


patch_dashboard_app_groups_url()
