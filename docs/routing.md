# Routing Conventions

## Route providers

Top-level application routes must be declared in `apps/<app>/routes.py` by exporting
`ROOT_URLPATTERNS`.

```python
from django.urls import include, path

ROOT_URLPATTERNS = [
    path("", include("apps.example.urls")),
]
```

`config/urls.py` is reserved for framework-level routes only (admin, i18n, debug toolbar,
static/media). Application routes are collected via `config.route_providers.autodiscovered_route_patterns()`.

## Legacy fallback removal and migration

Implicit fallback auto-includes for `apps/<app>/urls.py` and `apps/<app>/api/urls.py`
are removed. If an app still relies on fallback behavior, startup now raises
`ImproperlyConfigured` and names the affected app labels.

To migrate an app that previously depended on fallback:

1. Add `apps/<app>/routes.py`.
2. Define `ROOT_URLPATTERNS`.
3. Mount any existing URL modules explicitly inside `ROOT_URLPATTERNS`, for example:

```python
from django.urls import include, path

ROOT_URLPATTERNS = [
    path("example/", include("apps.example.urls")),
    path("example/api/", include("apps.example.api.urls")),
]
```

4. Restart and confirm route discovery succeeds without configuration errors.

## Route priority rules

Django resolves URL patterns in declaration order. Provider patterns are appended in
installed-app order, so the first matching pattern wins.

To avoid accidental shadowing:

1. Keep `path("")` providers limited to apps that intentionally own top-level pages.
2. Add highly specific prefixes before catch-all patterns inside each app's `urls.py`.
3. If two providers use `path("")`, ensure the higher-priority app appears first in
   `INSTALLED_APPS`.
4. Treat broad patterns like `<path:...>` as terminal patterns and place them last.
