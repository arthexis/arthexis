"""Nginx models exports."""

from apps.nginx.models.site_configuration import SiteConfiguration
from apps.nginx.parsers import parse_subdomain_prefixes

__all__ = ["SiteConfiguration", "parse_subdomain_prefixes"]
