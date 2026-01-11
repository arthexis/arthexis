from __future__ import annotations

import contextlib
import logging

from django.conf import settings
from django.core.validators import MaxLengthValidator, MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext, gettext_lazy as _, get_language_info

from apps.celery.utils import enqueue_task, is_celery_enabled
from apps.leads.models import Lead
from apps.repos import github
from apps.tasks.tasks import create_user_story_github_issue

logger = logging.getLogger(__name__)


class UserStory(Lead):
    path = models.CharField(max_length=500)
    name = models.CharField(max_length=40, blank=True)
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text=_("Rate your experience from 1 (lowest) to 5 (highest)."),
    )
    comments = models.TextField(
        validators=[MaxLengthValidator(400)],
        help_text=_("Share more about your experience."),
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

    class Meta:
        ordering = ["-submitted_at"]
        verbose_name = _("User Story")
        verbose_name_plural = _("User Stories")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        display = self.name or _("Anonymous")
        return f"{display} ({self.rating}/5)"

    def get_github_issue_labels(self) -> list[str]:
        """Return default labels used when creating GitHub issues."""

        return ["feedback"]

    def get_github_issue_fingerprint(self) -> str | None:
        """Return a fingerprint used to avoid duplicate issue submissions."""

        if self.pk:
            return f"user-story:{self.pk}"
        return None

    def build_github_issue_title(self) -> str:
        """Return the title used for GitHub issues."""

        path = self.path or "/"
        return gettext("Feedback for %(path)s (%(rating)s/5)") % {
            "path": path,
            "rating": self.rating,
        }

    def build_github_issue_body(self) -> str:
        """Return the issue body summarising the feedback details."""

        name = self.name or gettext("Anonymous")
        path = self.path or "/"
        lines = [
            f"**Path:** {path}",
            f"**Rating:** {self.rating}/5",
            f"**Name:** {name}",
        ]

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
            lines.append(f"**Submitted at:** {self.submitted_at.isoformat()}")

        comment = (self.comments or "").strip()
        if comment:
            lines.extend(["", comment])

        return "\n".join(lines).strip()

    def create_github_issue(self) -> str | None:
        """Create a GitHub issue for this feedback and store the identifiers."""

        if self.github_issue_url:
            return self.github_issue_url

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
        if self.rating >= 5:
            return False
        if self.github_issue_url:
            return False
        if not self.user_id:
            return False
        return is_celery_enabled()

    def enqueue_github_issue_creation(self) -> None:
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
