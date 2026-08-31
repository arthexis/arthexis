from __future__ import annotations

import contextlib
import logging
import re
from urllib.parse import urlparse

from django.apps import apps as django_apps
from django.conf import settings
from django.core.validators import (
    MaxLengthValidator,
    MaxValueValidator,
    MinValueValidator,
)
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import get_language_info, gettext
from django.utils.translation import gettext_lazy as _

from apps.celery.utils import enqueue_task, is_celery_enabled
from apps.core.models.lead_base import LeadBase

logger = logging.getLogger(__name__)
REPOS_APP_INSTALLED = django_apps.is_installed("apps.repos")

USER_STORY_GITHUB_BASE_LABELS = ("feedback",)
USER_STORY_GITHUB_RATING_LABELS = {
    5: ("enhancement", "priority: low"),
    4: ("enhancement",),
    3: ("bug", "priority: low"),
    2: ("bug",),
    1: ("bug", "priority: high"),
}
LOCAL_FEEDBACK_TAG = "local"
FEEDBACK_TAG_RE = re.compile(r"(?:^|\s)#(?P<tag>[A-Za-z][A-Za-z0-9_-]*)\b")


def parse_feedback_tags(*values: str) -> list[str]:
    """Return unique lower-case feedback tags parsed from free-form text."""

    tags: list[str] = []
    seen: set[str] = set()
    for value in values:
        for match in FEEDBACK_TAG_RE.finditer(value or ""):
            tag = match.group("tag").lower()
            if tag not in seen:
                seen.add(tag)
                tags.append(tag)
    return tags


class UserStory(LeadBase):
    class IssueDestination(models.TextChoices):
        GITHUB = "github", _("GitHub")
        LOCAL = "local", _("Local queue")

    path = models.CharField(max_length=500)
    name = models.CharField(max_length=40, blank=True)
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text=_("Rate your experience from 1 (lowest) to 5 (highest)."),
    )
    comments = models.TextField(help_text=_("Share more about your experience."))
    messages = models.TextField(
        blank=True,
        validators=[MaxLengthValidator(2000)],
        help_text=_("Messages displayed to the user when the feedback was submitted."),
    )
    contact_via_chat = models.BooleanField(
        default=False,
        db_default=False,
        help_text=_("Whether the submitter opted into chat follow-up during feedback."),
    )
    javascript_enabled = models.BooleanField(
        default=False,
        db_default=False,
        help_text=_("Whether JavaScript was enabled when feedback was submitted."),
    )
    screenshot = models.ImageField(
        upload_to="sites/user_story_screenshots/",
        blank=True,
        help_text=_("Optional screenshot captured while submitting feedback."),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="user_stories",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="owned_user_stories",
        help_text=_("Internal owner for this feedback."),
    )
    submitted_at = models.DateTimeField(auto_now_add=True)
    github_issue_number = models.PositiveIntegerField(
        blank=True,
        null=True,
        help_text=_("Number of the GitHub issue created for this feedback."),
    )
    github_issue_url = models.URLField(
        blank=True,
        help_text=_("Link to the GitHub issue created for this feedback."),
    )
    language_code = models.CharField(
        max_length=15,
        blank=True,
        help_text=_("Language selected when the feedback was submitted."),
    )
    feedback_tags = models.JSONField(
        blank=True,
        default=list,
        help_text=_("Hash tags parsed from feedback text, normalized without '#'."),
    )
    allow_feedback_issue_label_tags = models.BooleanField(
        default=False,
        db_default=False,
        help_text=_(
            "Whether parsed feedback hashtags may add matching existing GitHub "
            "labels when an issue is created."
        ),
    )
    issue_destination = models.CharField(
        max_length=16,
        choices=IssueDestination.choices,
        default=IssueDestination.GITHUB,
        db_index=True,
        help_text=_(
            "Where this feedback should be triaged. #local keeps it in the local queue."
        ),
    )

    class Meta:
        ordering = ["-submitted_at"]
        verbose_name = _("User Story")
        verbose_name_plural = _("User Stories")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        display = self.name or _("Anonymous")
        return f"{display} ({self.rating}/5)"

    def get_github_issue_labels(self) -> list[str]:
        """Return default labels used when creating GitHub issues."""

        labels = (
            *USER_STORY_GITHUB_BASE_LABELS,
            *USER_STORY_GITHUB_RATING_LABELS.get(self.rating, ()),
            *self.get_github_issue_label_tags(),
        )
        return list(dict.fromkeys(labels))

    def get_github_issue_label_tags(self) -> list[str]:
        """Return existing repository labels matched from privileged feedback tags."""

        if not self.allow_feedback_issue_label_tags:
            return []
        tags = self.feedback_tags or parse_feedback_tags(
            self.comments or "", self.messages or ""
        )
        if not tags or not REPOS_APP_INSTALLED:
            return []

        import requests

        from apps.repos import github
        from apps.repos.services.github import GitHubRepositoryError

        try:
            label_names = github.fetch_issue_label_names()
        except (GitHubRepositoryError, requests.RequestException) as exc:
            logger.warning(
                "Skipping feedback hashtag labels for user story %s: %s",
                self.pk or "unsaved",
                exc,
            )
            return []

        labels_by_tag = {}
        for label in label_names:
            cleaned = str(label or "").strip()
            if cleaned:
                labels_by_tag.setdefault(cleaned.casefold(), cleaned)

        matched_labels: list[str] = []
        for tag in tags:
            label = labels_by_tag.get(str(tag or "").casefold())
            if label and label not in matched_labels:
                matched_labels.append(label)
        return matched_labels

    def refresh_issue_routing(self) -> None:
        """Parse feedback tags and update the local/GitHub issue destination."""

        tags = parse_feedback_tags(self.comments or "", self.messages or "")
        self.feedback_tags = tags
        if LOCAL_FEEDBACK_TAG in tags:
            self.issue_destination = self.IssueDestination.LOCAL
        else:
            self.issue_destination = self.IssueDestination.GITHUB

    @property
    def is_local_issue(self) -> bool:
        """Return whether this feedback should stay in the local issue queue."""

        return self.issue_destination == self.IssueDestination.LOCAL

    def get_github_issue_fingerprint(self) -> str | None:
        """Return a fingerprint used to avoid duplicate issue submissions."""

        if self.pk:
            return f"user-story:{self.pk}"
        return None

    def build_github_issue_title(self) -> str:
        """Return the title used for GitHub issues."""

        return gettext("[%(node_role)s] Feedback for %(path)s (%(rating)s/5)") % {
            "node_role": self._feedback_node_role_label(),
            "path": self._github_issue_title_path(),
            "rating": self.rating,
        }

    def build_github_issue_body(self) -> str:
        """Return the issue body summarising the feedback details."""

        source = self._feedback_source_label()
        lines = [
            f"**Path:** {self._github_issue_path()}",
            f"**Source:** {source}",
            f"**Node role:** {self._feedback_node_role_label()}",
        ]
        if self.screenshot:
            lines.append("**Screenshot:** Provided (see admin attachments).")

        language_code = (self.language_code or "").strip()
        if language_code:
            normalized = language_code.replace("_", "-").lower()
            try:
                info = get_language_info(normalized)
            except KeyError:
                language_display = ""
            else:
                language_display = info.get("name_local") or info.get("name") or ""

            if language_display:
                lines.append(f"**Language:** {language_display} ({normalized})")
            else:
                lines.append(f"**Language:** {normalized}")

        if self.submitted_at:
            lines.append(f"**Submitted at:** {self._github_issue_submitted_at()}")

        message_list = (self.messages or "").strip()
        if message_list:
            lines.append(f"**Messages:** {message_list}")

        comment = (self.comments or "").strip()
        if comment:
            lines.extend(["", comment])

        return "\n".join(lines).strip()

    def _github_issue_path(self) -> str:
        raw_path = self.path or "/"
        parsed_path = urlparse(raw_path)
        if parsed_path.scheme and parsed_path.netloc:
            return parsed_path.path or "/"
        return raw_path

    def _github_issue_title_path(self) -> str:
        issue_path = self._github_issue_path()
        parsed_path = urlparse(issue_path)
        if parsed_path.query or parsed_path.fragment:
            return parsed_path.path or "/"
        return issue_path

    def _github_issue_submitted_at(self) -> str:
        submitted_at = self.submitted_at
        if timezone.is_aware(submitted_at):
            submitted_at = timezone.localtime(submitted_at)
        formatted = submitted_at.strftime("%Y-%m-%d %H:%M")
        timezone_name = submitted_at.strftime("%Z").strip()
        if timezone_name:
            return f"{formatted} {timezone_name}"
        return formatted

    def _feedback_node_role_label(self) -> str:
        if not REPOS_APP_INSTALLED:
            return gettext("unknown")

        from apps.repos.github_monitor import local_node_role

        if not hasattr(self, "_cached_node_role"):
            self._cached_node_role = local_node_role().strip()
        role = self._cached_node_role
        return role or gettext("unknown")

    def _feedback_source_label(self) -> str:
        """Return the best-effort source host for feedback issue summaries."""
        parsed_referer = urlparse(self.referer or "")
        if parsed_referer.hostname:
            return parsed_referer.hostname

        parsed_path = urlparse(self.path or "")
        if parsed_path.hostname:
            return parsed_path.hostname

        for domain in _extract_domains_from_text(self.messages or ""):
            return domain

        return "unknown"

    def create_github_issue(self) -> str | None:
        """Create a GitHub issue for this feedback and store the identifiers."""

        if self.github_issue_url:
            return self.github_issue_url
        if self.is_local_issue:
            logger.info(
                "Skipping GitHub issue creation for local user story %s", self.pk
            )
            return None
        if not REPOS_APP_INSTALLED:
            logger.info(
                "Skipping GitHub issue creation for user story %s; Repos app is not installed",
                self.pk,
            )
            return None

        from apps.repos import github

        response = github.create_issue(
            self.build_github_issue_title(),
            self.build_github_issue_body(),
            labels=self.get_github_issue_labels(),
            fingerprint=self.get_github_issue_fingerprint(),
        )

        if response is None:
            return None

        try:
            try:
                payload = response.json()
            except ValueError:  # pragma: no cover - defensive guard
                payload = {}
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                with contextlib.suppress(Exception):
                    close()

        issue_url = payload.get("html_url")
        issue_number = payload.get("number")

        update_fields: list[str] = []
        if issue_url and issue_url != self.github_issue_url:
            self.github_issue_url = issue_url
            update_fields.append("github_issue_url")
        if issue_number is not None and issue_number != self.github_issue_number:
            self.github_issue_number = issue_number
            update_fields.append("github_issue_number")

        if update_fields:
            self.save(update_fields=update_fields)

        return issue_url

    def should_enqueue_github_issue(self, *, created: bool, raw: bool) -> bool:
        if raw or not created:
            return False
        if self.github_issue_url:
            return False
        if self.is_local_issue:
            return False
        if not REPOS_APP_INSTALLED:
            return False
        if not self.user_id:
            return False
        return is_celery_enabled()

    def enqueue_github_issue_creation(self) -> None:
        from apps.sites.tasks import create_user_story_github_issue

        if not enqueue_task(
            create_user_story_github_issue, self.pk, require_enabled=False
        ):  # pragma: no cover - logging only
            logger.warning(
                "Failed to enqueue GitHub issue creation for user story %s", self.pk
            )

    def handle_post_save(self, *, created: bool, raw: bool) -> None:
        if not self.should_enqueue_github_issue(created=created, raw=raw):
            return
        self.enqueue_github_issue_creation()

    def save(self, *args, **kwargs) -> None:
        previous_issue_destination = None
        if self.pk:
            previous_issue_destination = (
                type(self)
                .all_objects.filter(pk=self.pk)
                .values_list("issue_destination", flat=True)
                .first()
            )
        update_fields = kwargs.get("update_fields")
        if isinstance(update_fields, str):
            update_field_names = [update_fields]
        elif update_fields is None:
            update_field_names = None
        else:
            update_field_names = list(update_fields)

        content_fields = {"comments", "messages"}
        should_refresh = update_field_names is None or bool(
            content_fields.intersection(update_field_names)
        )
        if should_refresh:
            self.refresh_issue_routing()
            if update_field_names is not None:
                kwargs["update_fields"] = [
                    *dict.fromkeys(
                        [*update_field_names, "feedback_tags", "issue_destination"]
                    )
                ]
        super().save(*args, **kwargs)
        if (
            previous_issue_destination != self.IssueDestination.LOCAL
            and self.is_local_issue
        ):
            from apps.core.services.operator_interrupts import (
                append_operator_local_feedback,
            )

            def append_local_feedback_interrupt() -> None:
                try:
                    append_operator_local_feedback(self)
                except OSError:
                    logger.warning(
                        "Unable to append operator local feedback interrupt for user story %s",
                        self.pk,
                        exc_info=True,
                    )

            transaction.on_commit(append_local_feedback_interrupt)


def _extract_domains_from_text(content: str) -> list[str]:
    """Return unique HTTP(S) domains parsed from free-form text."""

    domains: list[str] = []
    seen: set[str] = set()
    url_pattern = re.compile(r"https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+")
    for match in url_pattern.finditer(content):
        parsed = urlparse(match.group(0))
        if parsed.scheme in {"http", "https"} and parsed.hostname:
            if parsed.hostname not in seen:
                seen.add(parsed.hostname)
                domains.append(parsed.hostname)
    return domains


def user_story_attachment_upload_to(
    instance: UserStoryAttachment, filename: str
) -> str:
    """Return an upload path for feedback attachments."""

    story_id = instance.user_story_id or "unassigned"
    return f"sites/user_story_attachments/{story_id}/{filename}"


class UserStoryAttachment(models.Model):
    """File attached to a user feedback submission."""

    user_story = models.ForeignKey(
        UserStory,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    file = models.FileField(upload_to=user_story_attachment_upload_to)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["uploaded_at", "pk"]
        verbose_name = _("User Story Attachment")
        verbose_name_plural = _("User Story Attachments")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.file.name.rsplit("/", maxsplit=1)[-1]
