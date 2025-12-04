from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, override_settings
from django.urls import reverse

from apps.embeds import views


@pytest.mark.django_db
@override_settings(ALLOWED_HOSTS=["public.example.com"])
def test_embed_allows_host_with_port_when_request_is_allowed():
    """Embeds should permit the current host even when the target includes a port."""

    factory = RequestFactory()
    target = "https://public.example.com:8443/resources/123"
    request = factory.get(reverse("embeds:embed-card"), {"target": target}, HTTP_HOST="public.example.com")
    request.user = AnonymousUser()

    response = views.embed_card(request)

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert target in content
