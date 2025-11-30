import inspect

from django.core.checks import Warning, register
from django.urls.resolvers import URLPattern, URLResolver

from config import urls as project_urls


def _collect_checks(resolver: URLResolver, errors: list, prefix: str = ""):
    for pattern in resolver.url_patterns:
        if isinstance(pattern, URLResolver):
            _collect_checks(pattern, errors, prefix + pattern.pattern._route)
        elif isinstance(pattern, URLPattern):
            view = pattern.callback
            if getattr(view, "landing", False):
                sig = inspect.signature(view)
                params = list(sig.parameters.values())
                if params and params[0].name == "request":
                    params = params[1:]
                has_required = any(
                    p.default is inspect._empty
                    and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
                    for p in params
                )
                if has_required:
                    errors.append(
                        Warning(
                            f'Landing view "{view.__module__}.{view.__name__}" requires URL parameters and cannot be a landing page.',
                            id="pages.W001",
                        )
                    )


@register()
def landing_views_have_no_args(app_configs, **kwargs):
    errors: list = []
    for p in project_urls.urlpatterns:
        if isinstance(p, URLResolver):
            _collect_checks(p, errors, p.pattern._route)
    return errors
