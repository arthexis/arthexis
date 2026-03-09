import pytest
from django.contrib.auth.models import AnonymousUser
from django.db.utils import OperationalError, ProgrammingError
from django.test import RequestFactory

from apps.links.models.reference import Reference
from apps.links.templatetags.ref_tags import build_footer_context
from apps.modules.models import Module

pytestmark = pytest.mark.integration


@pytest.mark.django_db
def test_build_footer_context_falls_back_when_module_refs_hidden():
    module = Module.objects.create(path="/alpha/")
    general = Reference.objects.create(
        alt_text="General",
        value="https://example.com/general",
        include_in_footer=True,
    )
    specific = Reference.objects.create(
        alt_text="Module Private",
        value="https://example.com/module",
        include_in_footer=True,
        footer_visibility=Reference.FOOTER_PRIVATE,
    )
    specific.footer_modules.add(module)

    request = RequestFactory().get("/alpha/page/")
    request.user = AnonymousUser()

    context = build_footer_context(request=request)

    assert context["footer_refs"] == [general]


@pytest.mark.django_db
def test_get_current_module_prefers_request_attribute():
    requested_module = Module.objects.create(path="/requested/")
    Module.objects.create(path="/alpha/")
    requested_ref = Reference.objects.create(
        alt_text="Requested",
        value="https://example.com/requested",
        include_in_footer=True,
    )
    requested_ref.footer_modules.add(requested_module)

    request = RequestFactory().get("/alpha/page/")
    request.user = AnonymousUser()
    request.current_module = requested_module

    context = build_footer_context(request=request)

    assert context["current_module"] == requested_module
    assert context["footer_refs"] == [requested_ref]


@pytest.mark.django_db
def test_get_current_module_matches_longest_path_prefix():
    root = Module.objects.create(path="/alpha/")
    nested = Module.objects.create(path="/alpha/beta/")
    general = Reference.objects.create(
        alt_text="General",
        value="https://example.com/general-longest-prefix",
        include_in_footer=True,
    )
    nested_ref = Reference.objects.create(
        alt_text="Nested",
        value="https://example.com/nested",
        include_in_footer=True,
    )
    nested_ref.footer_modules.add(nested)

    request = RequestFactory().get("/alpha/beta/page/")
    request.user = AnonymousUser()

    context = build_footer_context(request=request)

    assert root.path != context["current_module"].path
    assert context["current_module"] == nested
    assert context["footer_refs"] == [nested_ref]
    assert general not in context["footer_refs"]


@pytest.mark.django_db
@pytest.mark.parametrize("db_error", [OperationalError, ProgrammingError])
def test_get_current_module_handles_database_errors(monkeypatch, db_error):
    general = Reference.objects.create(
        alt_text="General",
        value="https://example.com/general-fallback",
        include_in_footer=True,
    )

    request = RequestFactory().get("/alpha/page/")
    request.user = AnonymousUser()

    def raising_filter(*args, **kwargs):
        raise db_error("db unavailable")

    monkeypatch.setattr(
        "apps.links.templatetags.ref_tags.Module.objects.filter",
        raising_filter,
    )

    context = build_footer_context(request=request)

    assert context["current_module"] is None
    assert context["footer_refs"] == [general]
