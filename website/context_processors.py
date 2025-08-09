from django.contrib.sites.shortcuts import get_current_site


def nav_links(request):
    """Provide navigation links for the current site."""
    site = get_current_site(request)
    try:
        apps = site.apps.all()
    except Exception:
        apps = []
    return {"nav_apps": apps}
