import pytest

from apps.locals.user_data.seeds import _seed_fixture_has_unapplied_entries
from apps.nginx.models import SiteConfiguration


@pytest.mark.django_db
def test_seed_fixture_with_unique_field_is_skipped_when_already_loaded():
    SiteConfiguration.objects.create(name="preview-example")

    fixture_entries = [
        {
            "model": "nginx.siteconfiguration",
            "fields": {
                "name": "preview-example",
                "enabled": True,
                "mode": "public",
                "protocol": "http",
            },
        }
    ]

    assert _seed_fixture_has_unapplied_entries(fixture_entries) is False


@pytest.mark.django_db
def test_seed_fixture_with_unique_field_loads_when_missing():
    fixture_entries = [
        {
            "model": "nginx.siteconfiguration",
            "fields": {
                "name": "preview-example",
                "enabled": True,
                "mode": "public",
                "protocol": "http",
            },
        }
    ]

    assert _seed_fixture_has_unapplied_entries(fixture_entries) is True
