from __future__ import annotations

from decimal import Decimal
import json

import pytest
from django.urls import reverse

from apps.repos.models.repositories import GitHubRepository
from apps.repos.models.spam import RepositoryIssueSpamAssessment
from apps.repos.services import github as github_service
from apps.repos.spam_filter import evaluate_issue_payload, get_spam_policy


@pytest.mark.django_db
def test_evaluate_issue_payload_flags_obvious_spam(settings):
    settings.GITHUB_ISSUE_SPAM_KEYWORDS = ["free money"]
    settings.GITHUB_ISSUE_SPAM_MAX_LINKS = 1
    settings.GITHUB_ISSUE_SPAM_THRESHOLD = "0.60"

    result = evaluate_issue_payload(
        title="FREE MONEY now",
        body="visit https://spam.example and https://spam2.example",
        author="spam-account",
        policy=get_spam_policy(),
    )

    assert result.is_spam is True
    assert result.score >= Decimal("0.6")
    assert any(reason.startswith("keyword:") for reason in result.reasons)


@pytest.mark.django_db
def test_github_webhook_creates_spam_assessment(client, settings):
    settings.GITHUB_ISSUE_SPAM_FILTER_ENABLED = True
    settings.GITHUB_ISSUE_SPAM_AUTO_MODERATE = False
    settings.GITHUB_ISSUE_SPAM_MAX_LINKS = 0
    settings.GITHUB_ISSUE_SPAM_THRESHOLD = "0.20"

    repo = GitHubRepository.objects.create(owner="octocat", name="hello-world")
    url = reverse("repos:github-webhook")
    payload = {
        "action": "opened",
        "repository": {"owner": {"login": repo.owner}, "name": repo.name},
        "issue": {
            "number": 42,
            "title": "Check this offer",
            "body": "https://spam.example",
            "user": {"login": "spambot"},
        },
    }

    response = client.post(
        url,
        data=json.dumps(payload),
        content_type="application/json",
        **{"HTTP_X_GITHUB_EVENT": "issues", "HTTP_X_GITHUB_DELIVERY": "delivery-42"},
    )

    assert response.status_code == 200
    assessment = RepositoryIssueSpamAssessment.objects.get()
    assert assessment.repository == repo
    assert assessment.issue_number == 42
    assert assessment.is_spam is True
    assert assessment.delivery_id == "delivery-42"


@pytest.mark.django_db
def test_github_webhook_auto_moderates_when_enabled(client, monkeypatch, settings):
    settings.GITHUB_ISSUE_SPAM_FILTER_ENABLED = True
    settings.GITHUB_ISSUE_SPAM_AUTO_MODERATE = True
    settings.GITHUB_ISSUE_SPAM_MAX_LINKS = 0
    settings.GITHUB_ISSUE_SPAM_THRESHOLD = "0.20"
    settings.GITHUB_ISSUE_SPAM_AUTO_LABELS = ["spam-suspected", "triage"]

    repo = GitHubRepository.objects.create(owner="octocat", name="hello-world")
    url = reverse("repos:github-webhook")

    calls: list[tuple[str, int]] = []

    def fake_labels(*, owner, repository, issue_number, token, labels, timeout=10):
        del owner, repository, token, timeout
        calls.append(("labels", issue_number))
        assert tuple(labels) == ("spam-suspected", "triage")

    def fake_close(*, owner, repository, issue_number, token, timeout=10):
        del owner, repository, token, timeout
        calls.append(("close", issue_number))

    monkeypatch.setattr("apps.repos.spam_filter.github_service.get_github_issue_token", lambda: "token")
    monkeypatch.setattr("apps.repos.spam_filter.github_service.add_issue_labels", fake_labels)
    monkeypatch.setattr("apps.repos.spam_filter.github_service.close_issue", fake_close)

    payload = {
        "action": "opened",
        "repository": {"owner": {"login": repo.owner}, "name": repo.name},
        "issue": {
            "number": 77,
            "title": "promo",
            "body": "https://spam.example",
            "user": {"login": "spambot"},
        },
    }
    response = client.post(
        url,
        data=json.dumps(payload),
        content_type="application/json",
        **{"HTTP_X_GITHUB_EVENT": "issues", "HTTP_X_GITHUB_DELIVERY": "delivery-77"},
    )

    assert response.status_code == 200
    assert calls == [("labels", 77), ("close", 77)]


@pytest.mark.django_db
def test_github_webhook_auto_moderation_closes_issue_when_labeling_fails(
    client, monkeypatch, settings
):
    settings.GITHUB_ISSUE_SPAM_FILTER_ENABLED = True
    settings.GITHUB_ISSUE_SPAM_AUTO_MODERATE = True
    settings.GITHUB_ISSUE_SPAM_MAX_LINKS = 0
    settings.GITHUB_ISSUE_SPAM_THRESHOLD = "0.20"
    settings.GITHUB_ISSUE_SPAM_AUTO_LABELS = ["spam-suspected"]

    repo = GitHubRepository.objects.create(owner="octocat", name="hello-world")
    url = reverse("repos:github-webhook")

    calls: list[tuple[str, int]] = []

    def fake_labels(*, owner, repository, issue_number, token, labels, timeout=10):
        del owner, repository, issue_number, token, labels, timeout
        raise github_service.GitHubRepositoryError("labels failed")

    def fake_close(*, owner, repository, issue_number, token, timeout=10):
        del owner, repository, token, timeout
        calls.append(("close", issue_number))

    monkeypatch.setattr("apps.repos.spam_filter.github_service.get_github_issue_token", lambda: "token")
    monkeypatch.setattr("apps.repos.spam_filter.github_service.add_issue_labels", fake_labels)
    monkeypatch.setattr("apps.repos.spam_filter.github_service.close_issue", fake_close)

    payload = {
        "action": "opened",
        "repository": {"owner": {"login": repo.owner}, "name": repo.name},
        "issue": {
            "number": 88,
            "title": "promo",
            "body": "https://spam.example",
            "user": {"login": "spambot"},
        },
    }
    response = client.post(
        url,
        data=json.dumps(payload),
        content_type="application/json",
        **{"HTTP_X_GITHUB_EVENT": "issues", "HTTP_X_GITHUB_DELIVERY": "delivery-88"},
    )

    assert response.status_code == 200
    assert calls == [("close", 88)]
