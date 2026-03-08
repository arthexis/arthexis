from __future__ import annotations

import math
from dataclasses import dataclass

import markdown

from apps.content_suite.rendering import MARKDOWN_EXTENSIONS, sanitize_markdown_html
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _





def _sanitize_blog_html(html: str) -> str:
    """Sanitize rendered blog HTML before display."""

    return sanitize_markdown_html(html)


@dataclass(frozen=True)
class PublishResult:
    """Result metadata returned by scheduled publication helpers."""

    published_count: int


class BlogSeries(models.Model):
    """A long-running guide or topic stream for engineering-focused articles."""

    slug = models.SlugField(max_length=120, unique=True)
    title = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("title",)
        verbose_name = _("Blog series")
        verbose_name_plural = _("Blog series")

    def __str__(self) -> str:
        return self.title


class BlogTag(models.Model):
    """Tag used to classify articles by engineering domains."""

    slug = models.SlugField(max_length=80, unique=True)
    label = models.CharField(max_length=80, unique=True)

    class Meta:
        ordering = ("label",)

    def __str__(self) -> str:
        return self.label


class BlogArticleQuerySet(models.QuerySet):
    """Query helpers for article state transitions."""

    def published(self):
        return self.filter(status=BlogArticle.Status.PUBLISHED)

    def ready_to_publish(self):
        now = timezone.now()
        return self.filter(status=BlogArticle.Status.SCHEDULED, publish_at__lte=now)


class BlogArticle(models.Model):
    """Primary blog content model with editorial workflow and scheduling."""

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        IN_REVIEW = "in_review", _("In review")
        SCHEDULED = "scheduled", _("Scheduled")
        PUBLISHED = "published", _("Published")
        ARCHIVED = "archived", _("Archived")

    class BodyFormat(models.TextChoices):
        MARKDOWN = "markdown", _("Markdown")
        HTML = "html", _("HTML")

    slug = models.SlugField(max_length=150, unique=True, blank=True)
    title = models.CharField(max_length=220)
    subtitle = models.CharField(max_length=260, blank=True)
    excerpt = models.TextField(blank=True)
    body = models.TextField()
    body_format = models.CharField(
        max_length=16,
        choices=BodyFormat.choices,
        default=BodyFormat.MARKDOWN,
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="blog_articles",
    )
    reviewers = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="review_blog_articles",
    )
    series = models.ForeignKey(
        BlogSeries,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="articles",
    )
    tags = models.ManyToManyField(BlogTag, blank=True, related_name="articles")
    publish_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    canonical_url = models.URLField(blank=True)
    is_featured = models.BooleanField(default=False)
    allow_comments = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = BlogArticleQuerySet.as_manager()

    class Meta:
        ordering = ("-published_at", "-updated_at")

    def __str__(self) -> str:
        return self.title

    def get_absolute_url(self):
        return reverse("blog-detail", kwargs={"slug": self.slug})

    @property
    def body_as_html(self) -> str:
        """Render article body to safe HTML according to selected body format."""

        if self.body_format == self.BodyFormat.HTML:
            return _sanitize_blog_html(self.body or "")
        if self.body_format == self.BodyFormat.MARKDOWN:
            html = markdown.markdown(self.body or "", extensions=MARKDOWN_EXTENSIONS)
            return _sanitize_blog_html(html)

        return _sanitize_blog_html((self.body or "").replace("\n", "<br>"))

    @property
    def reading_time_minutes(self) -> int:
        """Estimate reading time using 220 words/minute."""

        count = len((self.body or "").split())
        return max(1, math.ceil(count / 220))

    def clean(self) -> None:
        """Validate scheduling and publication state consistency."""

        if self.status == self.Status.SCHEDULED and not self.publish_at:
            raise ValidationError({"publish_at": _("Scheduled articles need a publish date.")})
        if self.status == self.Status.PUBLISHED and not self.published_at:
            self.published_at = timezone.now()

    def save(self, *args, **kwargs):
        """Populate slug and normalize article transitions before persisting."""

        if not self.slug:
            self.slug = slugify(self.title)[:150]
        self.full_clean()
        return super().save(*args, **kwargs)

    @classmethod
    def publish_ready_articles(cls) -> PublishResult:
        """Publish all scheduled articles that are due."""

        now = timezone.now()
        updated = cls.objects.ready_to_publish().update(
            status=cls.Status.PUBLISHED,
            published_at=now,
        )
        return PublishResult(published_count=updated)


class BlogRevision(models.Model):
    """Immutable article snapshot saved before major edits."""

    article = models.ForeignKey(BlogArticle, on_delete=models.CASCADE, related_name="revisions")
    title = models.CharField(max_length=220)
    body = models.TextField()
    change_note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="blog_revisions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)


class BlogCodeReference(models.Model):
    """A code citation that can be embedded in article body via sigil token."""

    article = models.ForeignKey(BlogArticle, on_delete=models.CASCADE, related_name="code_references")
    label = models.CharField(max_length=140)
    repository_path = models.CharField(max_length=255)
    start_line = models.PositiveIntegerField(default=1)
    end_line = models.PositiveIntegerField(default=1)
    language = models.CharField(max_length=40, blank=True)
    highlight_lines = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ("repository_path", "start_line")

    def clean(self) -> None:
        """Validate citation line ranges."""

        super().clean()
        if self.end_line < self.start_line:
            raise ValidationError({"end_line": _("End line must be greater than or equal to start line.")})

    def save(self, *args, **kwargs):
        """Validate ranges before persisting."""

        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def sigil(self) -> str:
        """Return the authoring sigil token for this code reference."""

        return f"[CODE.{self.repository_path}:{self.start_line}-{self.end_line}]"


class BlogSigilShortcut(models.Model):
    """Specialized sigils that accelerate engineering article authoring."""

    article = models.ForeignKey(BlogArticle, on_delete=models.CASCADE, related_name="sigil_shortcuts")
    token = models.CharField(max_length=80)
    expansion_template = models.TextField(
        help_text=_("Template inserted when this token is resolved in article content."),
    )
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = ("article", "token")
        ordering = ("token",)

    def clean(self) -> None:
        """Ensure shortcut token matches SIGIL.NAME shape."""

        if "." not in self.token:
            raise ValidationError({"token": _("Token must include a sigil root and key, e.g. BLOG.SUMMARY")})
