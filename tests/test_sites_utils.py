import pytest

from django.contrib.sites.requests import RequestSite
from django.contrib.sites.models import Site
from django.db import DatabaseError
from django.http import HttpRequest

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
