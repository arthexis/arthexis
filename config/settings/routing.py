"""Route-provider settings assembled from the base provider list."""

from .base import ROUTE_PROVIDERS as BASE_ROUTE_PROVIDERS

ROUTE_PROVIDERS = [
    *BASE_ROUTE_PROVIDERS,
    "apps.release.routes",
    "apps.users.routes",
]
