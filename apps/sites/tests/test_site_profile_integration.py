import pytest
from django.contrib.sites.models import Site
from django.test import RequestFactory

from apps.sites.admin.forms import SiteForm
from apps.sites.models import SiteProfile
from config.middleware import SiteHttpsRedirectMiddleware

pytestmark = [pytest.mark.django_db]

def test_site_form_persists_profile_fields_when_saved_with_commit_false():
    site, _created = Site.objects.update_or_create(
        domain="profile-form.example.test",
        defaults={"name": "profile-form.example.test"},
    )
    SiteProfile.objects.create(site=site, require_https=False)

    form = SiteForm(
        data={
            "domain": "profile-form.example.test",
            "name": "profile-form.example.test",
            "managed": "on",
            "require_https": "on",
            "enable_public_chat": "on",
            "template": "",
            "default_landing": "",
            "interface_landing": "",
        },
        instance=site,
    )

    assert form.is_valid(), form.errors

    saved_site = form.save(commit=False)
    saved_site.save()
    form.save_m2m()

    profile = SiteProfile.objects.get(site=site)
    assert profile.managed is True
    assert profile.require_https is True
    assert profile.enable_public_chat is True

