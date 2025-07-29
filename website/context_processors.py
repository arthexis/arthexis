from django.contrib.sites.shortcuts import get_current_site
from .views import get_landing_apps

def nav_links(request):
    """Provide navigation links when visiting the main site."""
    site = get_current_site(request)
    app_name = site.name or "readme"
    if app_name in ("website", "readme"):
        return {"nav_apps": get_landing_apps()}
    return {}
