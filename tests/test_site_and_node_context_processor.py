import pytest
from django.core.exceptions import DisallowedHost
from django.test import RequestFactory

from config.context_processors import site_and_node


@pytest.mark.django_db
@pytest.mark.regression
def test_site_and_node_recovers_from_disallowed_host(monkeypatch):
    """Ensure badge context generation does not fail when host validation fails."""
    request = RequestFactory().get("/admin/", HTTP_HOST="invalid.example:8888")

    def _raise_disallowed_host():
        raise DisallowedHost("Invalid host header")

    monkeypatch.setattr(request, "get_host", _raise_disallowed_host)

    context = site_and_node(request)

    assert context["current_site_domain"] == "invalid.example"
