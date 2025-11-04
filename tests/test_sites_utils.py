import pytest

from django.conf import settings
from django.contrib.sites.requests import RequestSite
from django.contrib.sites.models import Site
from django.db import DatabaseError
from django.http import HttpRequest
from django.template import Context, Template

from core.templatetags.sites import get_current_site as tag_get_current_site
from utils.sites import get_site


def _build_request(host="testserver"):
    request = HttpRequest()
    request.META["HTTP_HOST"] = host
    return request


def test_get_site_returns_request_site_when_database_unavailable(monkeypatch):
    request = _build_request()

    def raise_database_error(*args, **kwargs):
        raise DatabaseError("no such table: django_site")

    def fail_if_called(*args, **kwargs):
        pytest.fail("get_current_site should not be called when the database is unavailable")

    monkeypatch.setattr("utils.sites.Site.objects.get", raise_database_error)
    monkeypatch.setattr("utils.sites.get_current_site", fail_if_called)

    site = get_site(request)

    assert isinstance(site, RequestSite)
    assert site.domain == "testserver"


def test_get_site_handles_database_error_during_current_site_lookup(monkeypatch):
    request = _build_request()

    def raise_does_not_exist(*args, **kwargs):
        raise Site.DoesNotExist()

    def raise_database_error(*args, **kwargs):
        raise DatabaseError("no such table: django_site")

    monkeypatch.setattr("utils.sites.Site.objects.get", raise_does_not_exist)
    monkeypatch.setattr("utils.sites.get_current_site", raise_database_error)

    site = get_site(request)

    assert isinstance(site, RequestSite)
    assert site.domain == "testserver"


def _render(template_string, context_dict):
    return Template(template_string).render(Context(context_dict))


def test_sites_template_tag_gets_current_site_for_request(monkeypatch):
    site = Site(domain="testserver", name="Test Server")
    monkeypatch.setattr(
        "core.templatetags.sites.site_utils.get_site", lambda request: site
    )

    tpl = "{% load sites %}{% get_current_site as current %}{{ current.domain }}"
    request = _build_request()
    rendered = _render(tpl, {"request": request})

    assert "testserver" in rendered


def test_sites_template_tag_without_request_uses_global_site(monkeypatch):
    site = Site(domain="example.test", name="Example")
    monkeypatch.setattr("core.templatetags.sites.Site.objects.get_current", lambda: site)

    tpl = "{% load sites %}{% get_current_site as current %}{{ current.domain }}"
    rendered = _render(tpl, {})

    assert "example.test" in rendered


def test_sites_template_tag_falls_back_to_request_site(monkeypatch):
    monkeypatch.setattr(
        "core.templatetags.sites.site_utils.get_site", lambda request: None
    )

    request = _build_request()
    context = Context({"request": request})
    site = tag_get_current_site(context)

    assert isinstance(site, RequestSite)
    assert site.domain == "testserver"


def test_get_site_uses_case_insensitive_domain_lookup(monkeypatch):
    request = _build_request("Example.COM:8888")
    recorded_kwargs: dict[str, object] = {}
    sentinel = Site(domain="example.com", name="Example")

    monkeypatch.setattr(settings, "ALLOWED_HOSTS", ["example.com"], raising=False)

    def fake_get(**kwargs):
        recorded_kwargs.update(kwargs)
        return sentinel

    def fail_if_called(*args, **kwargs):
        pytest.fail("get_current_site should not be consulted when domain lookup succeeds")

    monkeypatch.setattr("utils.sites.Site.objects.get", fake_get)
    monkeypatch.setattr("utils.sites.get_current_site", fail_if_called)

    resolved = get_site(request)

    assert resolved is sentinel
    assert "domain__iexact" in recorded_kwargs
    assert "domain" not in recorded_kwargs
