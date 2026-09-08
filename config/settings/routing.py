"""Route-provider settings assembled from the base provider list."""

from .base import ROUTE_PROVIDERS as _BASE_ROUTE_PROVIDERS

ROUTE_PROVIDERS = [
    *_BASE_ROUTE_PROVIDERS,
    "apps.analytics.routes",
    "apps.release.routes",
    "apps.users.routes",
]
