import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from apps.links.models import Reference
from apps.links.templatetags.ref_tags import _get_current_module, build_footer_context
from apps.modules.models import Module


@pytest.mark.django_db
def test_get_current_module_prefers_request_attribute():
    module = Module.objects.create(path="/alpha/")
    request = RequestFactory().get("/unrelated/")
    request.current_module = module

    assert _get_current_module(request) == module


@pytest.mark.django_db
def test_get_current_module_matches_longest_path_prefix():
    Module.objects.create(path="/alpha/")
    deepest = Module.objects.create(path="/alpha/beta/")
    request = RequestFactory().get("/alpha/beta/page/")

    assert _get_current_module(request) == deepest


@pytest.mark.django_db
def test_build_footer_context_uses_module_specific_refs_when_visible():
    module = Module.objects.create(path="/alpha/")
    general = Reference.objects.create(
        alt_text="General",
        value="https://example.com/general",
        include_in_footer=True,
    )
    specific = Reference.objects.create(
        alt_text="Module",
        value="https://example.com/module",
        include_in_footer=True,
    )
    specific.footer_modules.add(module)

    request = RequestFactory().get("/alpha/page/")
    request.user = AnonymousUser()

    context = build_footer_context(request=request)

    assert context["footer_refs"] == [specific]
    assert general not in context["footer_refs"]


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
