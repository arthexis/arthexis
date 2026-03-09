import pytest
from django.contrib.auth.models import AnonymousUser
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
