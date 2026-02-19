"""Models for the maximal blog experience."""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.template.defaultfilters import slugify
from django.urls import reverse
from django.utils import timezone


class BlogCategory(models.Model):
    """Top-level grouping used to organize posts."""

    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(max_length=90, unique=True, blank=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "blog categories"

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs) -> None:
        """Auto-generate a slug when not explicitly provided."""
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class BlogTag(models.Model):
    """Tag to add lightweight topic metadata to posts."""

    name = models.CharField(max_length=60, unique=True)
    slug = models.SlugField(max_length=70, unique=True, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs) -> None:
        """Persist a deterministic slug from the tag name."""
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class BlogSeries(models.Model):
    """Ordered collection that connects related posts."""

    title = models.CharField(max_length=150, unique=True)
    slug = models.SlugField(max_length=160, unique=True, blank=True)
    summary = models.TextField(blank=True)

    class Meta:
        verbose_name_plural = "blog series"

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs) -> None:
        """Create a slug from the series title when absent."""
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)


class BlogPostQuerySet(models.QuerySet):
    """Query helpers for selecting subsets of blog posts."""

    def published(self) -> "BlogPostQuerySet":
        """Return posts that have a publish timestamp in the past."""
        return self.filter(status=BlogPost.Status.PUBLISHED, published_at__lte=timezone.now())

    def featured(self) -> "BlogPostQuerySet":
        """Return highlighted posts only."""
        return self.filter(is_featured=True)


class BlogPost(models.Model):
    """Primary content model representing an article."""

    class Status(models.TextChoices):
        """Publication status for a post."""

        DRAFT = "draft", "Draft"
        REVIEW = "review", "In review"
        PUBLISHED = "published", "Published"
        ARCHIVED = "archived", "Archived"

    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    subtitle = models.CharField(max_length=220, blank=True)
    summary = models.TextField()
    body = models.TextField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    reading_time_minutes = models.PositiveSmallIntegerField(default=5)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_featured = models.BooleanField(default=False)
    allow_comments = models.BooleanField(default=True)

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="blog_posts",
    )
    category = models.ForeignKey(
        BlogCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="posts",
    )
    tags = models.ManyToManyField(BlogTag, related_name="posts", blank=True)
    series = models.ForeignKey(
        BlogSeries,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="posts",
    )
    series_order = models.PositiveIntegerField(null=True, blank=True)

    objects = BlogPostQuerySet.as_manager()

    class Meta:
        ordering = ["-published_at", "-created_at"]

    def __str__(self) -> str:
        return self.title

    def clean(self) -> None:
        """Validate model invariants before persisting."""
        if self.status == self.Status.PUBLISHED and self.published_at is None:
            raise ValidationError({"published_at": "Published posts require a publish date."})
        if self.series and self.series_order is None:
            raise ValidationError({"series_order": "Series posts require a sequence order."})

    def save(self, *args, **kwargs) -> None:
        """Set default values, validate, and save the post."""
        if not self.slug:
            self.slug = slugify(self.title)
        if self.status == self.Status.PUBLISHED and self.published_at is None:
            self.published_at = timezone.now()
        self.full_clean()
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        """Return canonical detail route for this post."""
        return reverse("blog:post-detail", kwargs={"slug": self.slug})


class BlogPostRevision(models.Model):
    """Immutable snapshot of content edits for auditability."""

    post = models.ForeignKey(BlogPost, on_delete=models.CASCADE, related_name="revisions")
    title = models.CharField(max_length=200)
    summary = models.TextField()
    body = models.TextField()
    editor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="blog_post_revisions",
    )
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class BlogComment(models.Model):
    """Reader comment posted on a specific blog article."""

    post = models.ForeignKey(BlogPost, on_delete=models.CASCADE, related_name="comments")
    author_name = models.CharField(max_length=80)
    author_email = models.EmailField()
    body = models.TextField()
    is_approved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class BlogReaction(models.Model):
    """Lightweight reaction to measure audience sentiment."""

    class Kind(models.TextChoices):
        """Supported reaction values."""

        LIKE = "like", "Like"
        INSIGHTFUL = "insightful", "Insightful"
        CELEBRATE = "celebrate", "Celebrate"

    post = models.ForeignKey(BlogPost, on_delete=models.CASCADE, related_name="reactions")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["post", "kind", "created_at"], name="blog_reaction_kind_created_unique"),
        ]
