from django.contrib.sites.models import Site
from django.contrib.sites.requests import RequestSite
from django.contrib.sites.shortcuts import get_current_site
from django.http.request import split_domain_port


def get_site(request):
    """Return the current Site ignoring any port in the host.

    Django's default ``get_current_site`` uses the full host string, which
    includes the port (e.g. ``"127.0.0.1:8000"``). If the ``Site`` domain is
    stored without the port, the lookup fails and a ``RequestSite`` is returned.
    This helper strips the port before performing the lookup so that hosts with
    ports still resolve to the correct ``Site`` instance.
    """
    host, _ = split_domain_port(request.get_host())
    try:
        return Site.objects.get(domain=host)
    except Site.DoesNotExist:
        try:
            return get_current_site(request)
        except Site.DoesNotExist:
            return RequestSite(request)
