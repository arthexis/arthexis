from utils.sites import get_site
from django.urls import Resolver404, resolve
from django.conf import settings
from pathlib import Path

_favicon_path = (
    Path(settings.BASE_DIR) / "website" / "fixtures" / "data" / "favicon.txt"
)
try:
    _DEFAULT_FAVICON = f"data:image/png;base64,{_favicon_path.read_text().strip()}"
except OSError:
    _DEFAULT_FAVICON = ""


def nav_links(request):
    """Provide navigation links for the current site."""
    site = get_site(request)
    try:
        applications = site.site_applications.select_related("application").all()
    except Exception:
        applications = []

    valid_apps = []
    current_app = None
    for app in applications:
        try:
            match = resolve(app.path)
        except Resolver404:
            continue
        view_func = match.func
        requires_login = getattr(view_func, "login_required", False) or hasattr(
            view_func, "login_url"
        )
        if requires_login and not request.user.is_authenticated:
            continue
        valid_apps.append(app)
        if request.path.startswith(app.path):
            if current_app is None or len(app.path) > len(current_app.path):
                current_app = app

    if current_app and current_app.favicon:
        favicon_url = current_app.favicon.url
    else:
        favicon_url = None
        if site:
            try:
                if site.badge.favicon:
                    favicon_url = site.badge.favicon.url
            except Exception:
                pass
        if not favicon_url:
            favicon_url = _DEFAULT_FAVICON

    return {"nav_apps": valid_apps, "favicon_url": favicon_url}
