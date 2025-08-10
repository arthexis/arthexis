from django.contrib.sites.shortcuts import get_current_site


def nav_links(request):
    """Provide navigation links for the current site."""
    site = get_current_site(request)
    try:
        applications = site.site_applications.select_related("application").all()
    except Exception:
        applications = []
    return {"nav_apps": applications}
