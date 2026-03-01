"""Admin stylesheet template tags and helpers."""

from django import template
from django.apps import apps
from django.conf import settings
from django.contrib.staticfiles import finders
from django.templatetags.static import static
from django.utils.html import format_html_join

register = template.Library()


def _resolve_current_admin_app_label(request) -> str:
    """Return the active admin app label inferred from request resolver metadata."""

    resolver_match = getattr(request, "resolver_match", None)
    if resolver_match is None:
        return ""

    app_label = resolver_match.kwargs.get("app_label")
    if isinstance(app_label, str) and app_label:
        return app_label

    url_name = resolver_match.url_name or ""
    if "_" not in url_name:
        return ""

    candidate_label = url_name.split("_", 1)[0]
    if not candidate_label:
        return ""

    try:
        apps.get_app_config(candidate_label)
    except LookupError:
        return ""
    return candidate_label


def _stylesheet_exists(stylesheet_path: str) -> bool:
    """Return whether a stylesheet path can be located via staticfiles finders."""

    if not stylesheet_path:
        return False
    return bool(finders.find(stylesheet_path))


def _configured_admin_stylesheets(app_label: str) -> list[str]:
    """Return framework/global/app stylesheet paths for the active admin context."""

    stylesheet_paths: list[str] = []

    configured_base = getattr(settings, "ADMIN_BASE_STYLESHEET", "")
    if configured_base:
        stylesheet_paths.append(configured_base)

    for stylesheet in getattr(settings, "ADMIN_GLOBAL_STYLESHEETS", []):
        if stylesheet and stylesheet not in stylesheet_paths:
            stylesheet_paths.append(stylesheet)

    app_stylesheets = getattr(settings, "ADMIN_APP_STYLESHEETS", {})
    configured_app_stylesheet = app_stylesheets.get(app_label, "") if app_label else ""
    if configured_app_stylesheet and configured_app_stylesheet not in stylesheet_paths:
        stylesheet_paths.append(configured_app_stylesheet)

    inferred_app_stylesheet = f"{app_label}/css/admin.css" if app_label else ""
    if (
        inferred_app_stylesheet
        and inferred_app_stylesheet not in stylesheet_paths
        and _stylesheet_exists(inferred_app_stylesheet)
    ):
        stylesheet_paths.append(inferred_app_stylesheet)

    return stylesheet_paths


@register.simple_tag(takes_context=True)
def render_admin_stylesheets(context) -> str:
    """Render stylesheet links for base, global, and active app-specific admin CSS."""

    request = context.get("request")
    active_app_label = _resolve_current_admin_app_label(request)
    stylesheet_paths = _configured_admin_stylesheets(active_app_label)
    return format_html_join(
        "\n",
        '<link rel="stylesheet" href="{}">',
        ((static(stylesheet_path),) for stylesheet_path in stylesheet_paths),
    )
