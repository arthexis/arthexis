from django.contrib.sites.shortcuts import get_current_site
from django.urls import Resolver404, resolve


def nav_links(request):
    """Provide navigation links for the current site."""
    site = get_current_site(request)
    try:
        applications = site.site_applications.select_related("application").all()
    except Exception:
        applications = []

    valid_apps = []
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

    return {"nav_apps": valid_apps}
