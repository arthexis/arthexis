from typing import Optional

from django.contrib.sites.models import Site
from django.contrib.sites.shortcuts import get_current_site
from django.db import DatabaseError
from django.http.request import split_domain_port


def get_site(request) -> Optional[Site]:
    """Return a real :class:`Site` instance for the request host.

    The lookup ignores any port component so ``127.0.0.1:8888`` matches a
    ``Site`` stored as ``127.0.0.1``.  If the host cannot be resolved to a real
    ``Site`` instance, ``None`` is returned instead of ``RequestSite`` so callers
    do not accidentally treat a placeholder object as a fully-populated model.
    """

    host, _ = split_domain_port(request.get_host())
    if host:
        try:
            return Site.objects.filter(domain__iexact=host).first()
        except DatabaseError:
            return None

    try:
        site = get_current_site(request)
    except (Site.DoesNotExist, DatabaseError):
        return None

    return site if isinstance(site, Site) else None
