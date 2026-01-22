from __future__ import annotations

import json
import urllib.parse

import pytest
from django.urls import reverse

from apps.repos.models.events import GitHubEvent
from apps.repos.models.repositories import GitHubRepository


@pytest.mark.django_db
def test_github_webhook_form_payload_array_is_preserved(client):
    repo = GitHubRepository.objects.create(owner="octocat", name="hello-world")

    url = reverse(
        "repos:github-webhook-repo", kwargs={"owner": repo.owner, "name": repo.name}
    )
    response = client.post(
        url,
        data=urllib.parse.urlencode(
            {"payload": json.dumps([{"action": "opened"}])}
        ),
        content_type="application/x-www-form-urlencoded",
    )

    assert response.status_code == 200
    event = GitHubEvent.objects.get()
    assert event.repository == repo
    assert event.payload == {"items": [{"action": "opened"}]}
    assert "payload=" in event.raw_body


@pytest.mark.django_db
def test_github_webhook_header_lookup_is_case_insensitive(client):
    repo = GitHubRepository.objects.create(owner="octocat", name="hello-world")
    url = reverse("repos:github-webhook")
    payload = {"repository": {"owner": {"login": repo.owner}, "name": repo.name}}

    response = client.post(
        url,
        data=json.dumps(payload),
        content_type="application/json",
        **{
            "HTTP_X_GITHUB_EVENT": "push",
            "HTTP_X_GITHUB_DELIVERY": "delivery-123",
            "HTTP_X_GITHUB_HOOK_ID": "hook-999",
            "HTTP_X_HUB_SIGNATURE": "sha1=abc",
            "HTTP_X_HUB_SIGNATURE_256": "sha256=def",
            "HTTP_USER_AGENT": "GitHub-Hookshot/1.0",
        },
    )

    assert response.status_code == 200
    event = GitHubEvent.objects.get()
    assert event.repository == repo
    assert event.event_type == "push"
    assert event.delivery_id == "delivery-123"
    assert event.hook_id == "hook-999"
    assert event.signature == "sha1=abc"
    assert event.signature_256 == "sha256=def"
    assert event.user_agent == "GitHub-Hookshot/1.0"
