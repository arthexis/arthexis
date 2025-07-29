from django.urls.resolvers import URLPattern, URLResolver
from typing import List, Dict, Optional, Iterable

from config import urls as project_urls


def footer_link(label: Optional[str] = None, column: str | None = ""):
    """Decorator to mark a view for inclusion in the footer."""

    def decorator(view):
        view.footer_link = True
        view.footer_label = label or view.__name__.replace("_", " ").title()
        view.footer_column = column or ""
        return view

    return decorator


def _get_patterns(resolver: URLResolver | Iterable) -> Iterable:
    if isinstance(resolver, URLResolver):
        return resolver.url_patterns
    if hasattr(resolver, "urlpatterns"):
        return resolver.urlpatterns
    return resolver


def _collect_links(resolver: URLResolver | Iterable, prefix: str = "") -> List[Dict]:
    links: List[Dict] = []
    for pattern in _get_patterns(resolver):
        if isinstance(pattern, URLResolver):
            links.extend(_collect_links(pattern, prefix + pattern.pattern._route))
        elif isinstance(pattern, URLPattern):
            view = pattern.callback
            if getattr(view, "footer_link", False):
                route = prefix + pattern.pattern._route
                if "<" in route:
                    continue
                links.append(
                    {
                        "name": getattr(view, "footer_label", pattern.name or route),
                        "path": "/" + route,
                        "column": getattr(view, "footer_column", ""),
                    }
                )
    return links


def get_footer_columns(resolver: URLResolver | None = None) -> List[Dict]:
    """Return footer links grouped by column."""

    resolver = resolver or project_urls
    links = _collect_links(resolver)
    columns: List[Dict] = []
    col_map: Dict[str, Dict] = {}
    for link in links:
        col = link["column"]
        if col not in col_map:
            entry = {"name": col, "links": []}
            col_map[col] = entry
            columns.append(entry)
        col_map[col]["links"].append({"name": link["name"], "path": link["path"]})
    return columns
