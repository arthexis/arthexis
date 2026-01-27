import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.contrib.sites.models import Site
from django.test import RequestFactory

from apps.modules.models import Module
from apps.sites.models import Landing, ReferrerLanding
from apps.sites.utils import REFERRER_LANDING_SESSION_KEY, get_referrer_landing
from apps.sites.views import landing as landing_views


@pytest.mark.django_db
def test_get_referrer_landing_persists_session():
    site, _ = Site.objects.get_or_create(
        domain="referrer-landing-a.test",
        defaults={"name": "Example"},
    )
    module = Module.objects.create(path="/apps/")
    landing = Landing.objects.create(module=module, path="/apps/", label="Apps")
    referrer = ReferrerLanding.objects.create(
        site=site,
        referrer_domain="ref.example.com",
        landing=landing,
    )

    request = RequestFactory().get(
        "/",
        HTTP_HOST="referrer-landing-a.test",
        HTTP_REFERER="https://ref.example.com/path",
    )
    SessionMiddleware(lambda req: None).process_request(request)
    request.session.save()

    match = get_referrer_landing(request, site)

    assert match == referrer
    assert request.session[REFERRER_LANDING_SESSION_KEY] == referrer.pk


@pytest.mark.django_db
def test_get_referrer_landing_from_session_without_referer():
    site, _ = Site.objects.get_or_create(
        domain="referrer-landing-b.test",
        defaults={"name": "Example"},
    )
    module = Module.objects.create(path="/apps/")
    landing = Landing.objects.create(module=module, path="/apps/", label="Apps")
    referrer = ReferrerLanding.objects.create(
        site=site,
        referrer_domain="ref.example.com",
        landing=landing,
    )

    request = RequestFactory().get("/", HTTP_HOST="referrer-landing-b.test")
    SessionMiddleware(lambda req: None).process_request(request)
    request.session[REFERRER_LANDING_SESSION_KEY] = referrer.pk
    request.session.save()

    match = get_referrer_landing(request, site)

    assert match == referrer


@pytest.mark.django_db
def test_index_redirects_to_referrer_landing(settings):
    settings.ALLOWED_HOSTS = ["referrer-landing-c.test"]
    site, _ = Site.objects.get_or_create(
        domain="referrer-landing-c.test",
        defaults={"name": "Example"},
    )
    module = Module.objects.create(path="/apps/")
    landing = Landing.objects.create(module=module, path="/apps/", label="Apps")
    ReferrerLanding.objects.create(
        site=site,
        referrer_domain="ref.example.com",
        landing=landing,
    )

    request = RequestFactory().get(
        "/",
        HTTP_HOST="referrer-landing-c.test",
        HTTP_REFERER="https://ref.example.com/path",
    )
    SessionMiddleware(lambda req: None).process_request(request)
    request.session.save()
    request.user = AnonymousUser()

    response = landing_views.index(request)

    assert response.status_code == 302
    assert response.url == "/apps/"
