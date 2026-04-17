from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity


class JobPosting(Entity):
    """Publicly visible opening that candidates can browse and apply to."""

    title = models.CharField(_("Title"), max_length=255)
    team = models.CharField(_("Team"), max_length=255, blank=True)
    location = models.CharField(_("Location"), max_length=255, blank=True)
    summary = models.TextField(_("Summary"))
    responsibilities = models.TextField(_("Responsibilities"), blank=True)
    requirements = models.TextField(_("Requirements"), blank=True)
    is_public = models.BooleanField(_("Public"), default=True)
    publish_at = models.DateTimeField(_("Publish at"), default=timezone.now)
    close_at = models.DateTimeField(_("Close at"), null=True, blank=True)

    created_at = models.DateTimeField(_("Created at"), auto_now_add=True)

    class Meta:
        ordering = ("title", "id")
        verbose_name = _("Job posting")
        verbose_name_plural = _("Job postings")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.title

    @property
    def is_open(self) -> bool:
        now = timezone.now()
        if self.publish_at and self.publish_at > now:
            return False
        if self.close_at and self.close_at < now:
            return False
        return self.is_public


class CVSubmission(Entity):
    """Candidate CV submission linked to an optional public job posting."""

    job_posting = models.ForeignKey(
        "jobs.JobPosting",
        on_delete=models.SET_NULL,
        related_name="cv_submissions",
        null=True,
        blank=True,
        verbose_name=_("Job posting"),
    )
    full_name = models.CharField(_("Full name"), max_length=255)
    email = models.EmailField(_("Email"))
    phone = models.CharField(_("Phone"), max_length=64, blank=True)
    cv_file = models.FileField(_("CV file"), upload_to="jobs/cv/")
    cover_letter = models.TextField(_("Cover letter"), blank=True)
    notes = models.TextField(_("Notes"), blank=True)

    created_at = models.DateTimeField(_("Created at"), auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")
        verbose_name = _("CV submission")
        verbose_name_plural = _("CV submissions")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.full_name} <{self.email}>"
