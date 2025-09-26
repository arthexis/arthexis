"""Template tags that expose site helpers used throughout the admin UI.

The Django admin templates in this project expect a ``sites`` template
library to be available.  Older deployments provided one, but the module
disappeared after recent dependency upgrades which caused ``{% load sites %}``
statements to fail with ``TemplateSyntaxError``.  This module restores the
library and keeps the API intentionally small: a ``get_current_site`` helper
mirroring the historic behaviour and a ``get_site_by_id`` helper for
compatibility with older templates.

Both helpers prefer the more resilient logic from :mod:`utils.sites` so they
continue to ignore port numbers and gracefully fall back to ``RequestSite``
objects when the database cannot be reached.
"""

from __future__ import annotations

from typing import Optional, Union

from django import template
from django.contrib.sites.models import Site
from django.contrib.sites.requests import RequestSite
from django.db import DatabaseError
from django.http import HttpRequest

from utils import sites as site_utils

register = template.Library()


SiteLike = Union[Site, RequestSite]


def _resolve_site(request: Optional[HttpRequest]) -> Optional[SiteLike]:
    """Return the best ``Site`` (or ``RequestSite``) for ``request``.

    When ``request`` is ``None`` we fall back to the project-wide current site.
    Any database errors are swallowed so template rendering never explodes.
    """

    if request is None:
        try:
            return Site.objects.get_current()
        except (Site.DoesNotExist, DatabaseError):
            return None

    site = site_utils.get_site(request)
    if isinstance(site, (Site, RequestSite)):
        return site
    return None


@register.simple_tag(takes_context=True)
def get_current_site(context: template.Context) -> Optional[SiteLike]:
    """Return the current site for the provided rendering context.

    The tag mirrors the historical behaviour of Django's ``sites`` template
    tag library so templates can still use ``{% get_current_site as site %}``
    to obtain the active ``Site`` or ``RequestSite`` instance.
    """

    request = context.get("request")
    site = _resolve_site(request)
    if site is None and isinstance(request, HttpRequest):
        return RequestSite(request)
    return site


@register.simple_tag
def get_site_by_id(site_id: Optional[Union[int, str]]) -> Optional[Site]:
    """Return the ``Site`` identified by ``site_id`` if it exists."""

    if not site_id:
        return None
    try:
        return Site.objects.get(pk=site_id)
    except (Site.DoesNotExist, ValueError, TypeError, DatabaseError):
        return None

