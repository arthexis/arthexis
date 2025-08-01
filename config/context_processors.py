from django.contrib.sites.models import Site
from django.http import HttpRequest


def site_and_node(request: HttpRequest):
    """Provide current Site and Node based on request host.

    Returns a dict with keys ``badge_site`` and ``badge_node``.
    ``badge_site`` is a ``Site`` instance or ``None`` if no match.
    ``badge_node`` is a ``Node`` instance or ``None`` if no match.
    ``badge_site_color`` and ``badge_node_color`` provide the configured colors.
    """
    host = request.get_host().split(':')[0]
    site = Site.objects.filter(domain__iexact=host).first()

    node = None
    try:
        from nodes.models import Node

        node = (
            Node.objects.filter(hostname__iexact=host).first()
            or Node.objects.filter(address=host).first()
        )
    except Exception:
        node = None

    site_color = "#28a745"
    if site:
        try:
            site_color = site.badge.badge_color
        except Exception:
            pass

    node_color = "#28a745"
    if node:
        node_color = node.badge_color

    return {
        "badge_site": site,
        "badge_node": node,
        "badge_site_color": site_color,
        "badge_node_color": node_color,
    }
