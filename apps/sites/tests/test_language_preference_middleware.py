import pytest
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import resolve

from apps.sites.middleware import LanguagePreferenceMiddleware


@pytest.mark.django_db
def test_language_prefix_sets_active_language(settings):
    settings.LANGUAGES = [("en", "English"), ("de", "German")]
    request = RequestFactory().get("/en/")
    request.resolver_match = resolve("/en/")

    middleware = LanguagePreferenceMiddleware(lambda _request: HttpResponse("ok"))
    response = middleware(request)

    assert response.status_code == 200
    assert request.LANGUAGE_CODE == "en"
    assert request.selected_language_code == "en"


@pytest.mark.django_db
def test_pages_requests_without_prefix_redirect_to_language_path(settings):
    settings.LANGUAGES = [("en-us", "English (US)"), ("fr", "French")]
    request = RequestFactory().get("/")
    request.resolver_match = resolve("/")
    request.COOKIES["django_language"] = "en-us"

    middleware = LanguagePreferenceMiddleware(lambda _request: HttpResponse("ok"))
    response = middleware(request)

    assert response.status_code == 302
    assert response["Location"] == "/en/"


@pytest.mark.django_db
def test_language_with_region_in_path_does_not_loop(settings):
    settings.LANGUAGES = [("en-us", "English (US)"), ("fr", "French")]
    request = RequestFactory().get("/en-us/")

    middleware = LanguagePreferenceMiddleware(lambda _request: HttpResponse("ok"))
    response = middleware(request)

    assert response.status_code == 200
    assert request.selected_language_code == "en"


@pytest.mark.django_db
def test_prefixed_pages_path_redirects_to_selected_language(settings):
    settings.LANGUAGES = [("en-us", "English (US)"), ("de", "German")]
    request = RequestFactory().get("/en/changelog/?v=1")
    request.resolver_match = resolve("/en/changelog/")
    request.COOKIES["django_language"] = "de"

    middleware = LanguagePreferenceMiddleware(lambda _request: HttpResponse("ok"))
    response = middleware(request)

    assert response.status_code == 302
    assert response["Location"] == "/de/changelog/?v=1"


@pytest.mark.django_db
def test_prefixed_pages_path_ignores_unsupported_selected_language(settings):
    settings.LANGUAGES = [("en", "English"), ("de", "German")]
    request = RequestFactory().get("/en/changelog/?v=1")
    request.resolver_match = resolve("/en/changelog/")
    request.COOKIES["django_language"] = "pt"

    middleware = LanguagePreferenceMiddleware(lambda _request: HttpResponse("ok"))
    response = middleware(request)

    assert response.status_code == 200
