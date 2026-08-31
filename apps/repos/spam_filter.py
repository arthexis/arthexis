"""Issue spam filtering utilities for GitHub webhook events."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from django.conf import settings

from apps.repos.models.events import GitHubEvent
from apps.repos.models.repositories import GitHubRepository
from apps.repos.models.spam import RepositoryIssueSpamAssessment
from apps.repos.services import github as github_service

URL_PATTERN = re.compile(r"https?://", re.IGNORECASE)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpamPolicy:
    """Configurable spam policy for GitHub issue moderation."""

    auto_label: tuple[str, ...]
    auto_moderate_enabled: bool
    max_links: int
    score_threshold: Decimal
    suspicious_keywords: tuple[str, ...]


@dataclass(frozen=True)
class SpamEvaluation:
    """Represents spam classifier output for one issue payload."""

    is_spam: bool
    reasons: tuple[str, ...]
    score: Decimal


DEFAULT_SPAM_KEYWORDS = (
    "airdrop",
    "casino",
    "crypto giveaway",
    "earn money fast",
    "free money",
    "seo service",
    "telegram",
)


def issue_spam_filter_enabled() -> bool:
    return bool(getattr(settings, "GITHUB_ISSUE_SPAM_FILTER_ENABLED", True))


def get_spam_policy() -> SpamPolicy:
    labels = tuple(
        str(label).strip()
        for label in getattr(settings, "GITHUB_ISSUE_SPAM_AUTO_LABELS", ("spam-suspected",))
        if str(label).strip()
    )
    keywords = tuple(
        str(keyword).lower().strip()
        for keyword in getattr(settings, "GITHUB_ISSUE_SPAM_KEYWORDS", DEFAULT_SPAM_KEYWORDS)
        if str(keyword).strip()
    )

    max_links = int(getattr(settings, "GITHUB_ISSUE_SPAM_MAX_LINKS", 2))
    threshold = Decimal(str(getattr(settings, "GITHUB_ISSUE_SPAM_THRESHOLD", "0.65")))

    return SpamPolicy(
        auto_label=labels,
        auto_moderate_enabled=bool(getattr(settings, "GITHUB_ISSUE_SPAM_AUTO_MODERATE", False)),
        max_links=max_links,
        score_threshold=threshold,
        suspicious_keywords=keywords,
    )


def evaluate_issue_payload(*, title: str, body: str, author: str, policy: SpamPolicy) -> SpamEvaluation:
    normalized_title = (title or "").strip().lower()
    normalized_body = (body or "").strip().lower()
    normalized_author = (author or "").strip().lower()
    text = "\n".join(part for part in (normalized_title, normalized_body) if part)

    reasons: list[str] = []
    score = Decimal("0.0")

    link_count = len(URL_PATTERN.findall(text))
    if link_count > policy.max_links:
        reasons.append(f"link_count>{policy.max_links}")
        score += Decimal("0.40")

    keyword_matches = [keyword for keyword in policy.suspicious_keywords if keyword in text]
    if keyword_matches:
        reasons.append("keyword:" + ",".join(sorted(set(keyword_matches))))
        score += Decimal("0.35")

    if normalized_author.endswith("bot") or normalized_author.startswith("spam"):
        reasons.append("suspicious_author")
        score += Decimal("0.20")

    if len(normalized_body) < 20 and link_count >= 1:
        reasons.append("short_body_with_link")
        score += Decimal("0.20")

    capped_score = min(score, Decimal("1.0"))
    quantized = capped_score.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    is_spam = quantized >= policy.score_threshold
    return SpamEvaluation(is_spam=is_spam, score=quantized, reasons=tuple(reasons))


def _issue_fields(payload: dict[str, Any]) -> tuple[int | None, str, str, str, str]:
    action = str(payload.get("action") or "")
    issue = payload.get("issue") or {}
    if not isinstance(issue, dict):
        return None, "", "", "", action

    number = issue.get("number")
    issue_number = number if isinstance(number, int) else None

    user = issue.get("user") or {}
    issue_author = str(user.get("login") or "") if isinstance(user, dict) else ""
    return (
        issue_number,
        str(issue.get("title") or ""),
        str(issue.get("body") or ""),
        issue_author,
        action,
    )


def _should_evaluate(*, event_type: str, action: str, issue_number: int | None) -> bool:
    if event_type != "issues" or issue_number is None:
        return False
    return action in {"opened", "edited", "reopened"}


def _moderate_issue(*, repository: GitHubRepository, issue_number: int, policy: SpamPolicy) -> None:
    if not policy.auto_moderate_enabled:
        return

    try:
        token = github_service.get_github_issue_token()
    except github_service.GitHubRepositoryError:
        logger.exception(
            "GitHub spam moderation token resolution failed for %s/%s#%s",
            repository.owner,
            repository.name,
            issue_number,
        )
        return

    if policy.auto_label:
        try:
            github_service.add_issue_labels(
                owner=repository.owner,
                repository=repository.name,
                issue_number=issue_number,
                token=token,
                labels=policy.auto_label,
            )
        except github_service.GitHubRepositoryError:
            logger.exception(
                "GitHub spam moderation labeling failed for %s/%s#%s",
                repository.owner,
                repository.name,
                issue_number,
            )

    try:
        github_service.close_issue(
            owner=repository.owner,
            repository=repository.name,
            issue_number=issue_number,
            token=token,
        )
    except github_service.GitHubRepositoryError:
        logger.exception(
            "GitHub spam moderation close failed for %s/%s#%s",
            repository.owner,
            repository.name,
            issue_number,
        )


def assess_github_issue_event(event: GitHubEvent) -> RepositoryIssueSpamAssessment | None:
    if not issue_spam_filter_enabled():
        return None

    payload = event.payload if isinstance(event.payload, dict) else {}
    issue_number, issue_title, issue_body, issue_author, action = _issue_fields(payload)

    if not _should_evaluate(
        event_type=str(event.event_type or ""),
        action=action,
        issue_number=issue_number,
    ):
        return None

    repository = event.repository
    if repository is None and event.owner and event.name:
        repository = GitHubRepository.objects.filter(owner=event.owner, name=event.name).first()
    if repository is None or issue_number is None:
        return None

    policy = get_spam_policy()
    result = evaluate_issue_payload(
        title=issue_title,
        body=issue_body,
        author=issue_author,
        policy=policy,
    )

    assessment, _created = RepositoryIssueSpamAssessment.objects.update_or_create(
        repository=repository,
        issue_number=issue_number,
        delivery_id=str(event.delivery_id or ""),
        defaults={
            "event": event,
            "action": action,
            "issue_author": issue_author,
            "issue_body": issue_body,
            "issue_title": issue_title,
            "is_spam": result.is_spam,
            "reasons": list(result.reasons),
            "score": result.score,
        },
    )

    if result.is_spam:
        _moderate_issue(repository=repository, issue_number=issue_number, policy=policy)

    return assessment
