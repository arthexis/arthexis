"""Tests enforcing routing-provider conventions for project URL configuration."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from config.route_providers import autodiscovered_route_patterns


def _extract_path_literals(node: ast.AST) -> list[str]:
    """Extract literal path prefixes from a list node used in urlpatterns updates."""

    if not isinstance(node, ast.List):
        return []

    values: list[str] = []
    for element in node.elts:
        if (
            isinstance(element, ast.Call)
            and isinstance(element.func, ast.Name)
            and element.func.id == "path"
            and element.args
            and isinstance(element.args[0], ast.Constant)
            and isinstance(element.args[0].value, str)
        ):
            values.append(element.args[0].value)
    return values


def _path_literals_from_urlpatterns_assignment(module_source: str) -> list[str]:
    """Extract literal framework path prefixes declared directly in ``config.urls``."""

    tree = ast.parse(module_source)
    values: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "urlpatterns":
                    values.extend(_extract_path_literals(node.value))
        elif isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "urlpatterns":
                values.extend(_extract_path_literals(node.value))
    return values


def test_config_urls_only_declares_framework_level_routes():
    """Regression: project URL root should not declare app-owned routes directly."""

    source = Path("config/urls.py").read_text(encoding="utf-8")
    direct_prefixes = _path_literals_from_urlpatterns_assignment(source)

    assert direct_prefixes == ["admin/", "i18n/setlang/", "admindocs/"]
    assert "include(\"apps." not in source
    assert "from apps." not in source


@pytest.mark.regression
def test_root_route_providers_keep_docs_blog_sites_priority_order():
    """Regression: broad ``path('')`` providers must keep deterministic priority order."""

    providers = [
        getattr(pattern.urlconf_name, "__name__", str(pattern.urlconf_name))
        for pattern in autodiscovered_route_patterns()
        if str(pattern.pattern) == ""
    ]

    assert providers[:3] == ["apps.blog.urls", "apps.docs.urls", "apps.sites.urls"]
