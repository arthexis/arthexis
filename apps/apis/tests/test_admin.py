"""Regression tests for API explorer admin configuration."""

from types import SimpleNamespace

from django.contrib import admin

from apps.apis import admin as apis_admin
from apps.apis.models import APIExplorer, ResourceMethod



def test_resource_method_inline_omits_heavy_fields_regression() -> None:
    """Regression: API explorer inline should hide large JSON and notes fields."""

    inline = apis_admin.ResourceMethodInline(APIExplorer, admin.site)

    assert inline.fields == ("operation_name", "resource_path", "http_method")
    assert inline.extra == 0



def test_resource_method_inline_exposes_change_link_regression() -> None:
    """Regression: API explorer inline rows should link to full method change form."""

    inline = apis_admin.ResourceMethodInline(APIExplorer, admin.site)

    assert inline.show_change_link is True



def test_resource_method_admin_retains_large_fields_for_drill_down_regression() -> None:
    """Regression: resource method admin should still expose detailed payload fields."""

    resource_admin = apis_admin.ResourceMethodAdmin(ResourceMethod, admin.site)

    request = SimpleNamespace(user=SimpleNamespace(has_perm=lambda perm: True))

    fields = set(resource_admin.get_fields(request=request))

    assert {"request_structure", "response_structure", "notes"}.issubset(fields)
