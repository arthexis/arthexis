"""Root route provider for public site and site-specific admin tools."""

from django.contrib import admin
from django.urls import include, path, re_path

from config.admin_urls import admin_route

from apps.sites.languages import get_supported_language_codes
from apps.sites import views as pages_views


def _supported_language_prefix_regex() -> str:
    """Return a strict two-letter regex for configured public languages."""

    language_codes = sorted(get_supported_language_codes())
    if not language_codes:
        return "en"
    return "|".join(language_codes)


PUBLIC_LANGUAGE_PREFIX_REGEX = _supported_language_prefix_regex()

ROOT_URLPATTERNS = [
    path(
        admin_route("user-tools/"),
        pages_views.admin_user_tools,
        name="admin-user-tools",
    ),
    path(
        admin_route("model-graph/<str:app_label>/"),
        admin.site.admin_view(pages_views.admin_model_graph),
        name="admin-model-graph",
    ),
    re_path(
        rf"^(?:{PUBLIC_LANGUAGE_PREFIX_REGEX})/",
        include(("apps.sites.urls", "pages"), namespace="pages-lang"),
    ),
    path("", include("apps.sites.urls")),
]
