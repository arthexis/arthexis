from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, override_settings
from django.urls import reverse

from apps.embeds import views
from apps.embeds.models import EmbedLead


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


@pytest.mark.django_db
@override_settings(ALLOWED_HOSTS=["public.example.com"])
def test_embed_adds_share_referer_and_tracks_lead():
    factory = RequestFactory()
    user = get_user_model().objects.create_user("alice", "alice@example.com", "password123")
    target = "https://public.example.com/resources/123#overview"
    request = factory.get(reverse("embeds:embed-card"), {"target": target}, HTTP_HOST="public.example.com")
    request.user = user

    response = views.embed_card(request)

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "?ref=alice" in content
    assert "#overview" in content

    lead = EmbedLead.objects.latest("id")
    assert lead.share_referer == "alice"
    assert lead.target_url == target
