from types import SimpleNamespace

import pytest
from django.http import HttpResponse
from django.test import RequestFactory, override_settings

from apps.sites.middleware import LanguagePreferenceMiddleware
from apps.sites.utils import (
    get_request_language_code,
    get_site_allowed_language_codes,
    get_site_default_language_code,
)
from apps.sites.views import i18n


@override_settings(LANGUAGES=(("en", "English"), ("es", "Spanish"), ("de", "German")))
def test_site_language_helpers_respect_allowed_and_default_values():
    site = SimpleNamespace(default_language="es", allowed_languages=["es", "de"])

    assert get_site_allowed_language_codes(site) == ("es", "de")
    assert get_site_default_language_code(site) == "es"


@override_settings(LANGUAGES=(("en", "English"), ("es", "Spanish"), ("de", "German")))
def test_get_request_language_code_falls_back_to_site_default_when_disallowed():
    request = RequestFactory().get("/", HTTP_HOST="example.com")
    request.site = SimpleNamespace(default_language="es", allowed_languages=["es", "de"])
    request.COOKIES["django_language"] = "en"

    assert get_request_language_code(request) == "es"


@override_settings(LANGUAGES=(("en", "English"), ("es", "Spanish"), ("de", "German")))
def test_set_language_replaces_disallowed_selection(monkeypatch):
    request = RequestFactory().post("/i18n/setlang/", {"language": "en", "next": "/"})
    request.site = SimpleNamespace(default_language="es", allowed_languages=["es", "de"])

    captured = {}

    def fake_set_language(passed_request):
        captured["language"] = passed_request.POST.get("language")
        return HttpResponse("ok")

    monkeypatch.setattr(i18n, "django_set_language", fake_set_language)

    response = i18n.set_language(request)

    assert response.status_code == 200
    assert captured["language"] == "es"


@override_settings(LANGUAGES=(("en", "English"), ("es", "Spanish"), ("de", "German")))
def test_language_preference_middleware_populates_site_languages():
    request = RequestFactory().get("/", HTTP_HOST="example.com")
    request.site = SimpleNamespace(default_language="es", allowed_languages=["es", "de"])
    request.COOKIES["django_language"] = "de"

    middleware = LanguagePreferenceMiddleware(lambda _request: HttpResponse("ok"))
    response = middleware(request)

    assert response.status_code == 200
    assert request.selected_language_code == "de"
    assert request.site_languages == (("es", "Spanish"), ("de", "German"))
