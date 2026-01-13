from __future__ import annotations

import fnmatch
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.links.models import Reference
from apps.media.models import MediaFile
from apps.media.utils import ensure_media_bucket

TERMS_DOCUMENT_BUCKET_SLUG = "terms-documents"
TERMS_ACCEPTANCE_BUCKET_SLUG = "terms-acceptance-documents"
TERMS_REGISTRATION_PHOTO_BUCKET_SLUG = "terms-registration-photos"

TERMS_DOCUMENT_ALLOWED_PATTERNS = "*.md\n*.markdown"
TERMS_ACCEPTANCE_ALLOWED_PATTERNS = "*"
TERMS_REGISTRATION_PHOTO_PATTERNS = "*.png\n*.jpg\n*.jpeg\n*.webp"


def ensure_terms_document_bucket():
    return ensure_media_bucket(
        slug=TERMS_DOCUMENT_BUCKET_SLUG,
        name="Terms Documents",
        allowed_patterns=TERMS_DOCUMENT_ALLOWED_PATTERNS,
    )


def ensure_terms_acceptance_bucket():
    return ensure_media_bucket(
        slug=TERMS_ACCEPTANCE_BUCKET_SLUG,
        name="Terms Acceptance Documents",
        allowed_patterns=TERMS_ACCEPTANCE_ALLOWED_PATTERNS,
    )


def ensure_terms_registration_photo_bucket():
    return ensure_media_bucket(
        slug=TERMS_REGISTRATION_PHOTO_BUCKET_SLUG,
        name="Terms Registration Photos",
        allowed_patterns=TERMS_REGISTRATION_PHOTO_PATTERNS,
    )


def matches_patterns(filename: str, patterns: str) -> bool:
    if not patterns:
        return True
    name = Path(filename).name
    entries = [value.strip() for value in patterns.splitlines() if value.strip()]
    return any(fnmatch.fnmatch(name, pattern) for pattern in entries)


class Term(models.Model):
    class Category(models.TextChoices):
        DRAFT = "draft", _("Draft")
        GENERAL = "general", _("General User")
        SECURITY_GROUP = "security_group", _("Security Group")
        REGISTRATION = "registration", _("Registration")

    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    summary = models.TextField(blank=True)
    document_media = models.ForeignKey(
        MediaFile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="term_documents",
        verbose_name=_("Document"),
    )
    body_text = models.TextField(blank=True)
    checkbox_text = models.CharField(max_length=200, default=_("I agree."))
    category = models.CharField(
        max_length=20, choices=Category.choices, default=Category.DRAFT
    )
    security_group = models.ForeignKey(
        Group,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="terms",
    )
    requires_document = models.BooleanField(default=False)
    required_document_label = models.CharField(max_length=200, blank=True)
    required_document_patterns = models.TextField(
        blank=True,
        help_text=_("Newline-separated file patterns (e.g. *.pdf)."),
    )
    required_document_min_bytes = models.PositiveBigIntegerField(
        null=True,
        blank=True,
        help_text=_("Minimum file size in bytes."),
    )
    required_document_max_bytes = models.PositiveBigIntegerField(
        null=True,
        blank=True,
        help_text=_("Maximum file size in bytes."),
    )
    reference = models.ForeignKey(
        Reference,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="terms",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title", "pk"]
        verbose_name = _("Term")
        verbose_name_plural = _("Terms")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.title

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        super().save(*args, **kwargs)
        if is_new and not self.reference_id and self.slug:
            self.reference = Reference.objects.create(
                alt_text=self.title,
                value=self.get_absolute_url(),
            )
            type(self).objects.filter(pk=self.pk).update(reference=self.reference)
        elif self.reference_id and self.slug:
            desired_value = self.get_absolute_url()
            if self.reference.value != desired_value:
                Reference.objects.filter(pk=self.reference_id).update(value=desired_value)

    def clean(self):
        super().clean()
        if self.category == self.Category.SECURITY_GROUP and not self.security_group_id:
            raise ValidationError(
                {"security_group": _("Security group is required for this category.")}
            )

    def get_absolute_url(self) -> str:
        return reverse("terms:detail", kwargs={"slug": self.slug})

    def load_markdown(self) -> str:
        if self.body_text:
            return self.body_text
        if self.document_media and self.document_media.file:
            with self.document_media.file.open("rb") as handle:
                return handle.read().decode("utf-8")
        return ""


class RegistrationSubmission(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        APPROVED = "approved", _("Approved")
        REJECTED = "rejected", _("Rejected")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="registration_submissions",
    )
    photo_media = models.ForeignKey(
        MediaFile,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="registration_photos",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    submitted_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_registrations",
    )
    review_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-submitted_at", "pk"]
        verbose_name = _("Registration Submission")
        verbose_name_plural = _("Registration Submissions")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.user} ({self.get_status_display()})"

    def mark_reviewed(self, reviewer, *, approved: bool, notes: str = ""):
        self.status = self.Status.APPROVED if approved else self.Status.REJECTED
        self.reviewed_by = reviewer
        self.reviewed_at = timezone.now()
        self.review_notes = notes
        self.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_notes"])


class TermAcceptance(models.Model):
    term = models.ForeignKey(Term, on_delete=models.CASCADE, related_name="acceptances")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="term_acceptances",
    )
    submission = models.ForeignKey(
        RegistrationSubmission,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="term_acceptances",
    )
    accepted_at = models.DateTimeField(auto_now_add=True)
    checkbox_text = models.CharField(max_length=200)
    ip_address = models.CharField(max_length=45, blank=True)
    user_agent = models.TextField(blank=True)
    required_document_media = models.ForeignKey(
        MediaFile,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="term_acceptance_documents",
    )

    class Meta:
        ordering = ["-accepted_at", "pk"]
        verbose_name = _("Term Acceptance")
        verbose_name_plural = _("Term Acceptances")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.term} - {self.user or 'anonymous'}"
