import pytest
from django.apps import apps


def test_site_configuration_is_not_registered_at_runtime():
    """The retired nginx app exposes migration history, not live models."""

    with pytest.raises(LookupError):
        apps.get_model("nginx", "SiteConfiguration")
